#!/usr/bin/env python3
"""Apply the exact build_02 -> build_03 two-instruction label move to idle RAM.
Reads USB state and code, patches only those RAM words; never writes flash.
"""
import argparse,json,struct
from pathlib import Path
import build_mk1_effects as b
from mk1_usb_flash import Session,sha,save,journal,ICACHE

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True); args=p.parse_args()
    old_dir=b.ROOT/'analysis/build_02_final'; new_dir=b.ROOT/'analysis/build_03'
    old=(old_dir/'image.bin').read_bytes(); new,m=b.build()
    if new!=(new_dir/'image.bin').read_bytes(): raise RuntimeError('candidate changed')
    previous=json.loads((old_dir/'manifest.json').read_text())
    if sha(old)!=previous['image_sha256']: raise RuntimeError('previous image hash changed')
    delta=[o for o in range(0,len(new),4) if old[o:o+4]!=new[o:o+4]]
    if delta!=[0x7b0b0,0x7b150]: raise RuntimeError('not the reviewed two-word label-only change')
    if struct.unpack_from('<I',new,delta[0])[0]!=0xd80ca820 or struct.unpack_from('<I',new,delta[1])[0]!=0x9dad0028:
        raise RuntimeError('unexpected revised instructions')
    stock=(b.ROOT/'flash_read1.bin').read_bytes(); args.output.mkdir(parents=True,exist_ok=False)
    s=Session(args.output,stock)
    report=dict(completed=False,flash_written=False,previous_candidate_dir=str(old_dir),
        previous_sha256=sha(old),candidate_sha256=sha(new),all_other_image_bytes_identical=True,
        delta=delta,card_absent_confirmed_by_user=True)
    try:
        s.open(); s.write_packet=512
        g=s.read(b.G,120)
        if g[88]!=0: raise RuntimeError('camera mode active')
        state=previous['symbols']['state']; current=s.read(state['ram'],32)
        if struct.unpack_from('<I',current,8)[0] or struct.unpack_from('<I',current,12)[0]:
            raise RuntimeError('native processing/snapshot still active')
        # Compare every installed patch, except the explicitly mutable 32-byte state.
        for patch in previous['patches']:
            off=patch['offset']; n=patch['length']; got=bytearray(s.read(off+b.BIAS,n)); expected=old[off:off+n]
            if off<=state['flash'] and state['flash']+32<=off+n:
                start=state['flash']-off; got[start:start+32]=expected[start:start+32]
            if bytes(got)!=expected: raise RuntimeError(f'RAM candidate preimage differs at {off:#x}')
        if s.read(0xf500+b.BIAS,4)!=struct.pack('<I',0x9c600003):
            raise RuntimeError('temporary disconnect route missing')
        report['USB_without_SD']=dict(vid=f'{s.dev.idVendor:04x}',pid=f'{s.dev.idProduct:04x}',
            interface=4,vendor_RAM_read_and_call_verified=True,stock_helpers_match=True)
        for off in delta:
            addr=off+b.BIAS; save(args.output/f'original-{off:06x}.bin',old[off:off+4])
            s.allowed.append((addr,addr+4)); journal(args.output,'RAM_label_revision_intent',address=addr)
            s.write(addr,new[off:off+4]); s.call(ICACHE)
            journal(args.output,'RAM_label_revision_verified',address=addr)
        report['completed']=True
        journal(args.output,'RAM_label_revision_complete',candidate_sha256=sha(new))
    except Exception as exc:
        report['error']=str(exc); journal(args.output,'stopped',error=str(exc)); raise
    finally:
        try:s.close()
        finally:(args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
if __name__=='__main__': main()
