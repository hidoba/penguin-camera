#!/usr/bin/env python3
"""MK1-only bounded USB flash backup/installation. No reset, retry or chip erase.
Normal USB transport and bridge ABI adapted from penguin tools/camera_usb.py.
Flash writes require explicit user approval for each requested phase.
"""
import argparse, hashlib, json, os, struct, sys
from pathlib import Path
import usb.core
import usb.util
from usb_probe import BIAS, MEM_WRAPPER, CHECKS, IDS
ROOT=Path(__file__).resolve().parents[1]
SIZE=0x400000; SECTOR=4096; CHUNK=65536; SETTINGS=0x2fd000
STOCK_SHA='33ac2db5716dacf06b60f97c5efbb2b2b77d820fe2b13e58c1eeb4379094fab5'
TARGET_SHA='c1dd84025bc7ea79e64bfb1e8f83ca028d63996f071b7e4682e29f380c605ec0'
READ=0x0203c698; ERASE=0x0203c890; PROGRAM=0x0203c7f4
FLUSH=0x0202a3bc; INVALIDATE=0x0202a438; ICACHE=0x0202a378
MALLOC=0x0203d8d0; FREE=0x0203d9a0
BEFORE=b'\xa5'*64; AFTER=b'\x5a'*64

def sha(b): return hashlib.sha256(b).hexdigest()
def save(p,b):
    with p.open('xb') as f: f.write(b); f.flush(); os.fsync(f.fileno())
def journal(out,event,**kw):
    with (out/'journal.jsonl').open('a') as f:
        f.write(json.dumps(dict(event=event,**kw))+'\n'); f.flush(); os.fsync(f.fileno())
def images():
    stock=(ROOT/'flash_read1.bin').read_bytes(); target=(ROOT/'analysis/build_01/image.bin').read_bytes()
    if len(stock)!=SIZE or sha(stock)!=STOCK_SHA or len(target)!=SIZE or sha(target)!=TARGET_SHA: raise RuntimeError('image hash/size gate failed')
    changed=[a for a in range(0,SIZE,SECTOR) if stock[a:a+SECTOR]!=target[a:a+SECTOR]]
    manifest=json.loads((ROOT/'analysis/build_01/manifest.json').read_text())
    if changed!=[int(a,16) for a in manifest['changed_sectors']]: raise RuntimeError('manifest mismatch')
    if stock[:0xc4000]!=target[:0xc4000] or SETTINGS in changed: raise RuntimeError('protected area changes')
    return stock,target,changed

class Code:
    def __init__(self,origin): self.origin=origin; self.words=[]; self.fix=[]; self.labels={}
    def emit(self,w): self.words.append(w&0xffffffff)
    def const(self,r,v): self.emit(6<<26|r<<21|(v>>16)&65535); self.emit(0x2a<<26|r<<21|r<<16|v&65535)
    def branch(self,op,label): self.fix.append((len(self.words),op,label)); self.emit(0)
    def label(self,label): self.labels[label]=len(self.words)
    def jal(self,target): self.emit(1<<26|((target-(self.origin+4*len(self.words)))//4)&0x3ffffff)
    def finish(self):
        for i,op,label in self.fix: self.words[i]=op<<26|(self.labels[label]-i)&0x3ffffff
        return struct.pack('<%dI'%len(self.words),*self.words)

def bridge(target,args,origin,result,bounds=None):
    c=Code(origin)
    if bounds:
        for r,v in enumerate(bounds,3):
            c.const(11,v); c.emit(0x39<<26|1<<21|r<<16|11<<11); c.branch(4,'deny')
    c.emit(0xd7e14ffc); c.emit(0x9c21fff8)
    for r,v in enumerate(args,3): c.const(r,v)
    c.jal(target); c.const(3,result); c.emit(0xd4035800)
    c.emit(0x9c801970); c.emit(0xd4032004)
    c.emit(0x9c210008); c.emit(0x8521fffc); c.emit(0x44004800)
    if bounds:
        c.label('deny'); c.const(3,result); c.emit(0x9d60ffff); c.emit(0xd4035800)
        c.emit(0x9c801970); c.emit(0xd4032004); c.emit(0x44004800)
    b=c.finish()
    if len(b)>512: raise RuntimeError('bridge overflow')
    return b

class Session:
    def __init__(self,out,stock):
        self.out=out; self.stock=stock; self.tag=6000; self.dev=None; self.claimed=False; self.detached=False; self.certain=True
        self.allowed=[]; self.owned=[]; self.backup=None; self.code=None; self.reader_code=None; self.write_packet=64
    def transact(self,address,length,data=None,callback=0xffffffff,timeout=30000,paced=False):
        self.tag+=1; self.certain=False
        cdb=b'\xcd'+struct.pack('<III',MEM_WRAPPER,address,callback)+b'\0'*3
        cbw=struct.pack('<4sIIBBB16s',b'USBC',self.tag,length,128 if data is None else 0,0,16,cdb)
        if self.dev.write(1,cbw,timeout=2000)!=31: raise RuntimeError('short CBW; no retry')
        if data is None:
            result=bytes(self.dev.read(0x81,length,timeout=timeout))
            if len(result)!=length: raise RuntimeError('short read; no retry')
        else:
            if paced:
                import time
                for i in range(0,length,512):
                    piece=data[i:i+512]
                    if self.dev.write(1,piece,timeout=2000)!=len(piece): raise RuntimeError('short RAM write; no retry')
                    if i+len(piece)<length: time.sleep(.002)
            elif self.dev.write(1,data,timeout=2000)!=length: raise RuntimeError('short RAM write; no retry')
            result=b''
        csw=bytes(self.dev.read(0x81,13,timeout=2000))
        if csw!=struct.pack('<4sIIB',b'USBS',self.tag,0,0): raise RuntimeError('invalid CSW; no retry')
        self.certain=True; return result
    def read(self,a,n): return b''.join(self.transact(a+i,min(4096,n-i)) for i in range(0,n,4096))
    def write(self,a,b,packet_size=None,paced=False):
        if not any(lo<=a and a+len(b)<=hi for lo,hi in self.allowed): raise RuntimeError('unowned RAM write')
        quantum=self.write_packet if packet_size is None else packet_size
        if quantum not in (64,512,4096): raise ValueError('invalid upload quantum')
        for i in range(0,len(b),quantum):
            packet=b[i:i+quantum]
            self.transact(a+i,len(packet),packet,FLUSH,paced=paced)
        got=self.read(a,len(b))
        if got!=b:
            self.certain=False
            save(self.out/f'ram-mismatch-{self.tag}-expected.bin',b)
            save(self.out/f'ram-mismatch-{self.tag}-actual.bin',got)
            journal(self.out,'ram_readback_mismatch',address=a,length=len(b),expected_sha256=sha(b),actual_sha256=sha(got))
            raise RuntimeError('RAM readback mismatch')
    def call(self,fn): return self.transact(self.result,8,callback=fn)
    def check(self,full=True):
        if struct.unpack('<I',self.read(0x020c7bd8,4))[0]!=self.desc or self.read(self.desc,48)!=self.descriptor: raise RuntimeError('USB display workspace changed')
        for _,off,n in (CHECKS+ [('complete cache helpers',0x2c978,0x13c),('complete allocator',0x3fed0,0x1bc),('display enable',0x3bf0c,0x38)] if full else []):
            if self.read(BIAS+off,n)!=self.stock[off:off+n]: raise RuntimeError('stock helper changed')
    def invoke(self,fn,args,bounds=None,provided=None):
        self.reader_code=None
        self.check(full=False); self.write(self.result,b'\0'*8)
        code=bridge(fn,args,self.code,self.result,bounds) if provided is None else provided
        self.write(self.code,code); self.call(ICACHE)
        value,marker=struct.unpack('<II',self.call(self.code))
        if marker!=0x1970: self.certain=False; raise RuntimeError('missing completion marker')
        return value
    def heap(self):
        node=struct.unpack('<I',self.read(0x020c7cb4,4))[0]; seen=set(); rows=[]
        while node:
            if node in seen or not 0x020c7cbc<=node<0x020c80bc or (node-0x020c7cbc)%16: raise RuntimeError('invalid heap node')
            seen.add(node); used,pointer,size,nxt=struct.unpack('<4I',self.read(node,16))
            if (used,pointer,size,nxt)==(1,0,0,0): break
            if used not in (0,1) or not size or not 0x020f0000<=pointer<=0x027fec00-size: raise RuntimeError('invalid heap record')
            rows.append((used,pointer,size)); node=nxt
        if not rows: raise RuntimeError('empty heap')
        return rows
    def allocate(self,n):
        n=(n+63)&~63; prior=self.heap(); p=self.invoke(MALLOC,[n,64]); current=self.heap()
        if p%64 or (1,p,n) not in current or any(u and p<q+k and q<p+n for u,q,k in prior):
            self.certain=False; raise RuntimeError('invalid/new allocation ownership')
        if {(q,k) for u,q,k in current if u}-{(q,k) for u,q,k in prior if u}!={(p,n)}: self.certain=False; raise RuntimeError('unexpected allocations')
        self.owned.append((p,n)); self.allowed.append((p,p+n)); journal(self.out,'ram_allocated',pointer=p,size=n); return p
    def open(self):
        devices=[d for v,p in IDS for d in usb.core.find(find_all=True,idVendor=v,idProduct=p)]
        if len(devices)!=1: raise RuntimeError('expected exactly one MK1 USB camera')
        self.dev=devices[0]
        intf=usb.util.find_descriptor(self.dev.get_active_configuration(),bInterfaceNumber=4,bAlternateSetting=0)
        if intf is None or (intf.bInterfaceClass,intf.bInterfaceSubClass,intf.bInterfaceProtocol)!=(8,6,80) or sorted((e.bEndpointAddress,e.bmAttributes) for e in intf)!=[(1,2),(129,2)]: raise RuntimeError('wrong MSC interface/endpoints')
        for node in Path('/sys/bus/usb/devices').iterdir():
            if ':' in node.name or not (node/'busnum').exists(): continue
            if int((node/'busnum').read_text())==self.dev.bus and int((node/'devnum').read_text())==self.dev.address:
                # Ejected media must report zero size, not merely an unmounted volume.
                if any(int(p.read_text()) for p in node.resolve().glob('**/block/*/size')): raise RuntimeError('SD media not ejected')
        if self.dev.is_kernel_driver_active(4): self.dev.detach_kernel_driver(4); self.detached=True
        usb.util.claim_interface(self.dev,4); self.claimed=True
        self.desc=struct.unpack('<I',self.read(0x020c7bd8,4))[0]
        if self.desc not in (0x020d2e7c,0x020d2eac): raise RuntimeError('unknown USB display descriptor')
        self.descriptor=self.read(self.desc,48); fields=struct.unpack('<12I',self.descriptor)
        p,n=fields[1],fields[6]
        if n!=0x1e000 or fields[8]!=0x014000f0 or (1,p,n) not in self.heap(): raise RuntimeError('invalid allocated USB framebuffer')
        self.code=p+n-512; self.result=self.code-64
        self.allowed=[(self.result,self.result+8),(self.code,self.code+512)]; self.check()
        self.backup=(self.read(self.result,8),self.read(self.code,512)); save(self.out/'scratch_before.bin',b''.join(self.backup))
        journal(self.out,'session_open',vid=self.dev.idVendor,pid=self.dev.idProduct,descriptor=self.desc,scratch=self.code)
        # Non-flash native-call rehearsal before malloc or SPI calls.
        c=Code(self.code); c.const(3,self.result); c.emit(0x9d605a17); c.emit(0xd4035800); c.emit(0x9c801970); c.emit(0xd4032004); c.emit(0x44004800)
        if self.invoke(0,[],provided=c.finish())!=0x5a17: self.certain=False; raise RuntimeError('native call rehearsal failed')
    def close(self):
        try:
            if self.certain and self.backup:
                for p,n in reversed(self.owned):
                    if (1,p,n) not in self.heap(): self.certain=False; raise RuntimeError('lost allocation ownership')
                    self.invoke(FREE,[p])
                    if (1,p,n) in self.heap(): self.certain=False; raise RuntimeError('allocation not freed')
                self.write(self.result,self.backup[0]); self.write(self.code,self.backup[1]); self.call(ICACHE)
                journal(self.out,'ram_cleanup_verified')
            elif not self.certain: print('KEEP CAMERA POWERED: uncertain completion; no retry or RAM cleanup.',flush=True)
        finally:
            if self.claimed: usb.util.release_interface(self.dev,4)
            if self.detached and self.certain: self.dev.attach_kernel_driver(4)
            if self.dev is not None: usb.util.dispose_resources(self.dev)

def read_flash(s,base,off,n,poison):
    from mk1_flash_reader import reader
    if off%16 or n%16 or not 16<=n<=CHUNK or not 0<=off<=SIZE-n: raise ValueError('invalid flash read')
    s.check(full=False)
    code=reader(s.code,s.result,base,n)
    if s.reader_code!=code:
        s.write(s.code,code);s.call(ICACHE);s.reader_code=code
    s.write(s.result,struct.pack('<II',off,poison*0x01010101))
    status,marker=struct.unpack('<II',s.call(s.code))
    if status or marker!=0x1970:
        s.certain=False;raise RuntimeError('native SPI reader failed')
    got=s.read(base,n+128)
    if got[:64]!=BEFORE or got[-64:]!=AFTER:
        s.certain=False
        save(s.out/f'dma-guard-mismatch-{s.tag}.bin',got)
        journal(s.out,'dma_guard_mismatch',flash_offset=off,length=n)
        raise RuntimeError('DMA guards corrupted')
    return got[64:-64]

def snapshot(s,base,out,name,passno):
    data=bytearray()
    with (out/(name+'.partial')).open('xb') as partial:
        for off in range(0,SIZE,CHUNK):
            if off%(512*1024)==0: s.check()
            chunk=read_flash(s,base,off,CHUNK,((off//CHUNK)+127*passno)&255)
            data+=chunk;partial.write(chunk)
            if (off+CHUNK)%(512*1024)==0:
                partial.flush();os.fsync(partial.fileno())
                print(f'{name}: {(off+CHUNK)//1024}/4096 KiB',flush=True)
        partial.flush();os.fsync(partial.fileno())
    save(out/name,data); return bytes(data)

def guard(origin,fn,bounds):
    c=Code(origin)
    for r,v in enumerate(bounds,3):
        c.const(11,v); c.emit(0x39<<26|1<<21|r<<16|11<<11); c.branch(4,'deny')
    c.emit(((fn-(origin+4*len(c.words)))//4)&0x3ffffff)
    c.label('deny'); c.emit(0x9d60ffff); c.emit(0x44004800)
    return c.finish()

def phases(changed):
    return {'relocated':[a for a in changed if a>=0x2fe000],
            'inplace':[a for a in changed if a<0x2fe000 and a!=0xd1000],
            'directory':[0xd1000]}

def install(args,stock,target,changed):
    if not args.approved_phase: raise RuntimeError('phase needs explicit user approval')
    report=json.loads((args.backup/'report.json').read_text())
    backup=(args.backup/'backup.bin').read_bytes()
    if not report['completed'] or not report.get('reads_identical') or sha(backup)!=report['read1_sha256'] or len(backup)!=SIZE: raise RuntimeError('invalid verified backup')
    if (args.backup/'flash-read-1.bin').read_bytes()!=backup or (args.backup/'flash-read-2.bin').read_bytes()!=backup: raise RuntimeError('backup files differ')
    records=[json.loads(line) for line in (args.backup/'journal.jsonl').read_text().splitlines()]
    if not records or records[-1]['event']!='ram_cleanup_verified': raise RuntimeError('backup cleanup not verified')
    normalized=bytearray(backup); normalized[SETTINGS:SETTINGS+SECTOR]=stock[SETTINGS:SETTINGS+SECTOR]
    if normalized!=stock: raise RuntimeError('unknown backup outside settings')
    expected=bytearray(target);expected[SETTINGS:SETTINGS+SECTOR]=backup[SETTINGS:SETTINGS+SECTOR]
    groups=phases(changed); preceding=bytearray(backup)
    for phase,addresses in groups.items():
        if phase==args.phase: break
        for a in addresses: preceding[a:a+SECTOR]=expected[a:a+SECTOR]
    selected=groups[args.phase]; staged=bytearray(preceding)
    for a in selected: staged[a:a+SECTOR]=expected[a:a+SECTOR]
    if args.resume_state:
        partial=args.resume_state.read_bytes()
        if len(partial)!=SIZE: raise RuntimeError('invalid partial image size')
        for a in range(0,SIZE,SECTOR):
            acceptable=[bytes(preceding[a:a+SECTOR])]
            if a in selected: acceptable.append(bytes(staged[a:a+SECTOR]))
            if partial[a:a+SECTOR] not in acceptable: raise RuntimeError('partial image outside reviewed transitions')
        preceding=bytearray(partial)
        selected=[a for a in selected if preceding[a:a+SECTOR]!=staged[a:a+SECTOR]]
    if any(a<0xc4000 or a==SETTINGS or a not in changed for a in selected): raise RuntimeError('protected/unreviewed sector selected')
    args.output.mkdir(parents=True,exist_ok=False); s=Session(args.output,stock)
    result=dict(completed=False,phase=args.phase,flash_written=False,sectors=[hex(a) for a in selected])
    mutation=False
    try:
        save(args.output/'before-expected.bin',preceding);save(args.output/'after-expected.bin',staged)
        s.open();base=s.allocate(CHUNK+128); private=s.allocate(512); source=s.allocate(SECTOR+128)
        a=snapshot(s,base,args.output,'before-read-1.bin',0);b=snapshot(s,base,args.output,'before-read-2.bin',1)
        if a!=b or a!=preceding: raise RuntimeError('fresh paired preimage differs; nothing written')
        journal(args.output,'phase_approved',phase=args.phase,sectors=result['sectors'])
        for n,off in enumerate(selected,1):
            before=bytes(preceding[off:off+SECTOR]); after=bytes(staged[off:off+SECTOR])
            if read_flash(s,base,off,SECTOR,0x71)!=before: raise RuntimeError('sector changed before write')
            # Upload and verify before erase; keep it separate from the SPI read buffer.
            raw=BEFORE+after+AFTER
            s.write(source,raw,packet_size=args.source_packet,paced=args.paced_upload)
            erasecode=guard(private,ERASE,[off]); programcode=guard(private,PROGRAM,[off,source+64,SECTOR])
            # A wrong address must be refused, with no call into a stock write helper.
            s.write(private,erasecode);s.call(ICACHE)
            if s.invoke(private,[0])!=0xffffffff: s.certain=False; raise RuntimeError('erase refusal failed')
            s.write(private,programcode);s.call(ICACHE)
            if s.invoke(private,[off,source+128,SECTOR])!=0xffffffff: s.certain=False; raise RuntimeError('program refusal failed')
            if s.read(source,len(raw))!=raw: raise RuntimeError('source changed before erase')
            journal(args.output,'sector_intent',address=off,before_sha256=sha(before),after_sha256=sha(after))
            if before!=b'\xff'*SECTOR:
                s.write(private,erasecode);s.call(ICACHE);s.check()
                journal(args.output,'erase_intent',address=off);mutation=True;result['flash_written']=True
                status=s.invoke(private,[off]);journal(args.output,'erase_return',address=off,status=status)
                if status or read_flash(s,base,off,SECTOR,0x26)!=b'\xff'*SECTOR: raise RuntimeError('erase verification failed')
            if s.read(source,len(raw))!=raw: raise RuntimeError('source changed before program')
            s.invoke(FLUSH,[source,len(raw)])
            s.write(private,programcode);s.call(ICACHE);s.check()
            journal(args.output,'program_intent',address=off);mutation=True;result['flash_written']=True
            status=s.invoke(private,[off,source+64,SECTOR]);journal(args.output,'program_return',address=off,status=status)
            if status or s.read(source,len(raw))!=raw or s.read(private,len(programcode))!=programcode: raise RuntimeError('program/source guards failed')
            for poison in (0x36,0xc9):
                if read_flash(s,base,off,SECTOR,poison)!=after: raise RuntimeError('sector readback mismatch')
            journal(args.output,'sector_verified',address=off)
            print(f'{args.phase}: sector {off:#08x} verified ({n}/{len(selected)})',flush=True)
        final=snapshot(s,base,args.output,'full-readback.bin',2)
        if final!=staged: raise RuntimeError('whole staged image mismatch')
        result.update(completed=True,readback_sha256=sha(final),matches_original_target=final==target,
                      matches_target_with_live_settings=final==expected,settings_preserved=final[SETTINGS:SETTINGS+SECTOR]==backup[SETTINGS:SETTINGS+SECTOR])
        journal(args.output,'whole_phase_verified',sha256=sha(final))
    except Exception as e:
        result['error']=str(e)
        if mutation: s.certain=False
        journal(args.output,'stopped',error=str(e),transport_certain=s.certain);raise
    finally:
        try: s.close()
        finally: (args.output/'report.json').write_text(json.dumps(result,indent=2)+'\n')


def main():
    ap=argparse.ArgumentParser(description=__doc__); ap.add_argument('command',choices=['backup','install']); ap.add_argument('--output',type=Path,required=True); ap.add_argument('--backup',type=Path); ap.add_argument('--phase',choices=['relocated','inplace','directory']); ap.add_argument('--approved-phase',action='store_true'); ap.add_argument('--resume-state',type=Path); ap.add_argument('--source-packet',type=int,choices=[512],default=512); ap.add_argument('--paced-upload',action='store_true'); args=ap.parse_args()
    stock,target,changed=images()
    if args.command=='install':
        if not args.backup or not args.phase: ap.error('install requires --backup and --phase')
        install(args,stock,target,changed); return
    args.output.mkdir(parents=True,exist_ok=False); s=Session(args.output,stock)
    report={'flash_written':False,'completed':False}
    try:
        s.open(); base=s.allocate(CHUNK+128)
        a=snapshot(s,base,args.output,'flash-read-1.bin',0); b=snapshot(s,base,args.output,'flash-read-2.bin',1)
        differences=[hex(i) for i in range(0,SIZE,SECTOR) if a[i:i+SECTOR]!=stock[i:i+SECTOR]]
        report.update(reads_identical=a==b,read1_sha256=sha(a),read2_sha256=sha(b),stock_sha256=sha(stock),stock_differing_sectors=differences,settings_only_difference=all(int(i,16)==SETTINGS for i in differences))
        if a!=b: raise RuntimeError('independent flash reads differ')
        save(args.output/'backup.bin',a)
        if not report['settings_only_difference']: raise RuntimeError('flash differs outside settings; investigate before installation')
        expected=bytearray(target); expected[SETTINGS:SETTINGS+SECTOR]=a[SETTINGS:SETTINGS+SECTOR]; save(args.output/'expected-target.bin',expected)
        report.update(completed=True,expected_target_sha256=sha(expected),changed_sector_count=len(changed),order=[hex(i) for i in sorted(changed,key=lambda i:(i==0xd1000,i<0x2fe000,i))])
    except Exception as e:
        report['error']=str(e); journal(args.output,'stopped',error=str(e),transport_certain=s.certain); raise
    finally:
        try: s.close()
        finally: (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n'); print(json.dumps(report,indent=2),flush=True)
if __name__=='__main__': main()
