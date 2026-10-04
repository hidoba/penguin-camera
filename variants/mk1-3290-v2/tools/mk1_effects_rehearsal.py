#!/usr/bin/env python3
"""Run the candidate pixel routines in owned temporary RAM. No flash writes or live hooks.
Requires ejected SD volume. Restores USB scratch and frees all owned allocations.
"""
import argparse, json, random, struct, time
from pathlib import Path
import build_mk1_effects as build
from mk1_usb_flash import Session, save, journal, sha, ICACHE, FREE
from test_mk1_effects import floyd
from penguin_port import color_gray, gray_filters, tone_gray, date_stamp
from penguin_port.effects_v5 import halftone, bayer8_thresholds
GUARD_A=b'\xa5'*64; GUARD_Z=b'\x5a'*64

def expected(src,w,h,mode,step):
    y=color_gray.reference(src,w,h)
    if mode==0: return y
    y=gray_filters.apply_reference(y,0 if mode==1 else 2,step)
    if mode==1:
        t=bayer8_thresholds(); return bytes(255 if v>=t[(i//w%8)*8+i%w%8] else 0 for i,v in enumerate(y))
    if mode==2: return halftone(y,w,h)
    return floyd(y,w,h)

def main():
    ap=argparse.ArgumentParser(description=__doc__); ap.add_argument('--output',type=Path,required=True); args=ap.parse_args()
    stock=(build.ROOT/'flash_read1.bin').read_bytes()
    if sha(stock)!=build.STOCK_SHA: raise RuntimeError('stock hash gate failed')
    image,manifest=build.build(); args.output.mkdir(parents=True,exist_ok=False)
    save(args.output/'candidate-manifest.json',json.dumps(manifest,indent=2).encode()); s=Session(args.output,stock)
    report=dict(flash_written=False,live_hooks_installed=False,completed=False,candidate_sha256=sha(image),cases=[])
    old_base=build.BASE
    try:
        s.open(); s.write_packet=512; heap=s.heap(); report['largest_free_usb_heap']=max(n for used,_,n in heap if not used)
        # Compiler relocation covers every native code/data address; nothing is published to live camera paths.
        code=s.allocate(build.SIZE); build.BASE=code; payload,layout,lengths=build.payload()
        s.write(code,payload,packet_size=512); s.call(ICACHE)
        def invoke(name,params):
            return s.invoke(code+layout[name],params)
        # Native independent geometry tests, including preview and print sizes.
        for w,h,modes,context in ((64,32,(0,1,2,3),2),(320,180,(1,2,3),0),(384,216,(1,2,3),2)):
            n=w*h; allocation=s.allocate(n*3//2+128); y=allocation+64; uv=y+n
            rng=random.Random(w+h); src=rng.randbytes(n*3//2)
            for mode in modes:
                raw=GUARD_A+src+GUARD_Z; s.write(allocation,raw,packet_size=512)
                start=time.monotonic(); invoke('transform',[y,w,h,uv,context,mode]); elapsed=time.monotonic()-start
                got=s.read(allocation,len(raw)); target=expected(src,w,h,mode,8 if context==2 else 4)
                if got[:64]!=GUARD_A or got[-64:]!=GUARD_Z or got[64:64+n]!=target or got[64+n:-64]!=bytes([128])*(n//2):
                    save(args.output/f'failure-{w}-{h}-{mode}.bin',got); raise RuntimeError('native pixel/guard mismatch')
                # Processor allocates/releases scratch internally; caller retains only its own heap blocks.
                if {(p,n) for u,p,n in s.heap() if u and any(p==q for q,_ in s.owned)}!=set(s.owned):
                    s.certain=False; raise RuntimeError('owned heap blocks changed')
                item=dict(width=w,height=h,mode=mode,context=context,elapsed_seconds=round(elapsed,3),pixels_match=True,guards_match=True)
                report['cases'].append(item); journal(args.output,'native_case_verified',**item)
                print(item,flush=True)
        # Two independent reads of the immutable boot ROM; no ROM-entry/reset call.
        rom1=s.read(0x00100000,0x8000); rom2=s.read(0x00100000,0x8000)
        if rom1!=rom2: raise RuntimeError('boot ROM reads differ')
        save(args.output/'rom-read-1.bin',rom1); save(args.output/'rom-read-2.bin',rom2)
        report['rom_sha256']=sha(rom1)
        # Printer tone and date primitives, without invoking the printer/head driver.
        w,h=216,384; n=w*h; allocation=s.allocate(n+128); y=allocation+64
        src=bytes((i*17+i//w*13)%256 for i in range(n)); s.write(allocation,GUARD_A+src+GUARD_Z,packet_size=512)
        invoke('tone',[y,w,h]); got=s.read(allocation,n+128)
        if got!=GUARD_A+tone_gray.reference(src,w,h,tone_gray.tables())+GUARD_Z: raise RuntimeError('native tone mismatch')
        s.write(allocation,GUARD_A+src+GUARD_Z,packet_size=512)
        year,month,day=struct.unpack('<HBB',s.read(0x020c7c74,4)); enabled=int.from_bytes(s.read(0x020c1ab8,4),'little')==date_stamp.SETTING_ON
        invoke('date',[y,w,h]); got=s.read(allocation,n+128)
        if got!=GUARD_A+date_stamp.reference(src,w,h,year,month,day,enabled)+GUARD_Z: raise RuntimeError('native date mismatch')
        report.update(completed=True,native_tone_matches=True,native_date_matches=True)
        journal(args.output,'rehearsal_verified')
    except Exception as exc:
        report['error']=str(exc); journal(args.output,'rehearsal_stopped',error=str(exc)); raise
    finally:
        build.BASE=old_base
        try: s.close()
        finally: (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
if __name__=='__main__': main()
