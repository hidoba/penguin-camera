"""Resident, bounded USB operations: reduce round trips without changing SPI timing.
No automatic calls or hardware writes when imported. Both write entry points need
an exact approved sector list, an owned source, correct request seal and source guards.
"""
import struct, time
from mk1_native import Code
from mk1_usb_flash import SIZE, SECTOR, CHUNK, SETTINGS, ERASE, PROGRAM, ICACHE, BEFORE, AFTER, save, journal
from mk1_flash_reader import reader

def writer(origin,result,source,sectors,program,resource=False):
    ranges=((origin,512),(result,8),(source,SECTOR+128))
    if origin%64 or result%4 or source%64 or any(not 0x020f0000<=p<=0x027fec00-n for p,n in ranges):
        raise ValueError('writer memory outside aligned SDK heap')
    if any(p<q+m and q<p+n for j,(p,n) in enumerate(ranges) for q,m in ranges[j+1:]):
        raise ValueError('writer memory overlaps')
    low, high = (0xc4000, SIZE) if resource else (0x3000, 0xc4000)
    if not sectors or len(set(sectors))!=len(sectors) or any(o%4096 or not low<=o<high or o==SETTINGS for o in sectors):
        raise ValueError('unapproved/protected sector')
    a=Code(); i=a.immediate
    a.store(9,1,-4); i(0x27,1,1,-8)
    a.const(11,result); a.compare(3,11,1); a.branch(4,'deny')
    i(0x2f,1,4,8); a.branch(4,'deny'); i(0x21,6,3,0); i(0x21,7,3,4)
    a.alu(11,6,7,5); a.const(12,0xffffffff); a.compare(11,12,1); a.branch(4,'deny')
    for off in sectors:
        a.const(11,off); a.compare(6,11,0); a.branch(4,'owned')
    a.branch(0,'deny'); a.label('owned')
    for tag,pointer,pattern in (('before',source,0xa5a5a5a5),('after',source+64+4096,0x5a5a5a5a)):
        a.const(7,pointer); a.const(8,pointer+64); a.const(11,pattern)
        a.label(tag); i(0x21,12,7,0); a.compare(12,11,1); a.branch(4,'deny')
        i(0x27,7,7,4); a.compare(7,8,4); a.branch(4,tag)
    i(0x2a,3,6,0)
    if program: a.const(4,source+64); i(0x27,5,0,4096)
    a.call(origin,PROGRAM if program else ERASE); a.branch(0,'finish')
    a.label('deny'); i(0x27,11,0,-1)
    a.label('finish'); a.const(3,result); a.store(11,3,0); i(0x27,4,0,0x1970); a.store(4,3,4)
    i(0x27,1,1,8); i(0x21,9,1,-4); a.emit(0x44004800)
    blob=a.finish()
    if len(blob)>512: raise ValueError('resident writer overflow')
    return blob

class Operations:
    def __init__(self,s,buffer,source,sectors,resource=False):
        if any(not any(p<=a and a+n<=p+k for p,k in s.owned)
               for a,n in ((buffer,CHUNK+128),(source,SECTOR+128))):
            raise ValueError('resident buffers are not owned allocations')
        if buffer<source+SECTOR+128 and source<buffer+CHUNK+128:
            raise ValueError('resident read/source buffers overlap')
        self.s=s; self.buffer=buffer; self.source=source; self.resource=resource; self.memory=s.allocate(2048)
        self.entry={name:self.memory+i*512 for i,name in enumerate(('read_full','read_sector','erase','program'))}
        self.codes={
            'read_full':reader(self.entry['read_full'],s.result,buffer,CHUNK),
            'read_sector':reader(self.entry['read_sector'],s.result,buffer,SECTOR),
            'erase':writer(self.entry['erase'],s.result,source,sectors,False,resource),
            'program':writer(self.entry['program'],s.result,source,sectors,True,resource)}
        for name,blob in self.codes.items(): s.write(self.entry[name],blob,packet_size=512)
        s.call(ICACHE); journal(s.out,'resident_operations_verified',entries=self.entry,approved_sectors=sectors)
    def approve(self,sectors):
        """Replace only the exact-sector write guards; keep resident read routines."""
        codes={name:writer(self.entry[name],self.s.result,self.source,sectors,
                           name=='program',self.resource) for name in ('erase','program')}
        for name,blob in codes.items(): self.s.write(self.entry[name],blob,packet_size=512)
        self.s.call(ICACHE); self.codes.update(codes)
        journal(self.s.out,'resident_write_guards_verified',approved_sectors=sectors)
    def call(self,name,off,seal):
        self.s.check(full=False); self.s.write(self.s.result,struct.pack('<II',off,seal))
        started=time.monotonic(); status,marker=struct.unpack('<II',self.s.call(self.entry[name]))
        if marker!=0x1970: self.s.certain=False; raise RuntimeError('resident completion marker missing')
        return status,time.monotonic()-started
    def read(self,off,n,poison):
        if n not in (SECTOR,CHUNK) or off%16 or not 0<=off<=SIZE-n: raise ValueError('invalid resident read range')
        status,_=self.call('read_full' if n==CHUNK else 'read_sector',off,poison*0x01010101)
        if status: self.s.certain=False; raise RuntimeError('resident SPI read failed')
        got=self.s.read(self.buffer,n+128)
        if got[:64]!=BEFORE or got[-64:]!=AFTER:
            self.s.certain=False; save(self.s.out/f'resident-read-guard-{self.s.tag}.bin',got); raise RuntimeError('DMA guards changed')
        return got[64:-64]
    def write(self,name,off):
        if name not in ('erase','program'): raise ValueError('unknown write operation')
        if self.s.read(self.entry[name],len(self.codes[name]))!=self.codes[name]: raise RuntimeError('resident writer changed')
        return self.call(name,off,off^0xffffffff)
    def refuse(self,name):
        status,_=self.call(name,0,0xffffffff)
        if status!=0xffffffff: self.s.certain=False; raise RuntimeError('resident write refusal failed')
