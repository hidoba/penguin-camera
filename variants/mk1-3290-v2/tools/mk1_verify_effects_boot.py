#!/usr/bin/env python3
"""Verify installed effects RAM after restart, without RAM/flash writes or callbacks.
SD absence is a user-confirmed physical fact, recorded via --sd-absent.
"""
import argparse,json,struct
from pathlib import Path
import usb.core,usb.util
from usb_probe import IDS,CHECKS,BIAS,read_mem
from mk1_usb_flash import STOCK_SHA,sha,save
import build_mk1_effects as builder

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--candidate',type=Path,required=True); p.add_argument('--output',type=Path,required=True)
    p.add_argument('--sd-absent',action='store_true'); a=p.parse_args()
    target,m=builder.build(); stock=(builder.ROOT/'flash_read1.bin').read_bytes()
    if target!=(a.candidate/'image.bin').read_bytes() or m!=json.loads((a.candidate/'manifest.json').read_text()):
        raise RuntimeError('candidate differs from reproducible build')
    if sha(stock)!=STOCK_SHA: raise RuntimeError('stock hash changed')
    a.output.mkdir(parents=True,exist_ok=False)
    report=dict(completed=False,flash_written=False,RAM_written=False,native_callbacks_used=False,
        candidate_sha256=sha(target),user_confirmed_SD_absent=a.sd_absent,patches=[])
    dev=None;claimed=False;detached=False;tag=12000
    try:
        devices=[d for v,pid in IDS for d in usb.core.find(find_all=True,idVendor=v,idProduct=pid)]
        if len(devices)!=1: raise RuntimeError('expected one storage-mode camera')
        dev=devices[0]; intf=usb.util.find_descriptor(dev.get_active_configuration(),bInterfaceNumber=4,bAlternateSetting=0)
        if intf is None or (intf.bInterfaceClass,intf.bInterfaceSubClass,intf.bInterfaceProtocol)!=(8,6,80):
            raise RuntimeError('storage interface 4 missing')
        for node in Path('/sys/bus/usb/devices').iterdir():
            if ':' in node.name or not (node/'busnum').exists(): continue
            if int((node/'busnum').read_text())==dev.bus and int((node/'devnum').read_text())==dev.address:
                if any(int(v.read_text()) for v in node.resolve().glob('**/block/*/size')):
                    raise RuntimeError('SD volume must be ejected before claiming storage interface')
        if dev.is_kernel_driver_active(4): dev.detach_kernel_driver(4);detached=True
        usb.util.claim_interface(dev,4);claimed=True
        report['USB']=dict(vid=f'{dev.idVendor:04x}',pid=f'{dev.idProduct:04x}',interface=4,bus=dev.bus,address=dev.address)
        def read(address,n):
            nonlocal tag
            got=bytearray()
            for start in range(0,n,4096):
                tag+=1; got+=read_mem(dev,tag,address+start,min(4096,n-start))
            return bytes(got)
        state=m['symbols']['state']; mutable=None
        for patch in m['patches']:
            off=patch['offset']; n=patch['length']; got=bytearray(read(off+BIAS,n));save(a.output/f'ram-{off:06x}.bin',got)
            expected=target[off:off+n]
            if off<=state['flash'] and state['flash']+state['length']<=off+n:
                k=state['flash']-off; mutable=bytes(got[k:k+32]);got[k:k+32]=expected[k:k+32]
            if got!=expected: raise RuntimeError(f'RAM patch mismatch at flash offset {off:#x}')
            report['patches'].append(dict(flash_offset=off,length=n,matched=True))
        for name,off,n in CHECKS:
            if read(BIAS+off,n)!=stock[off:off+n]: raise RuntimeError('stock helper mismatch: '+name)
        # Confirm the RAM-only disconnect test override was never installed in flash.
        if read(BIAS+0xf500,4)!=target[0xf500:0xf504]: raise RuntimeError('temporary disconnect override remains')
        report.update(completed=True,stock_helpers_match=True,temporary_disconnect_override_absent=True,
            mutable_state_words=list(struct.unpack('<8I',mutable)) if mutable else None)
    except Exception as exc:
        report['error']=str(exc); raise
    finally:
        try:
            if claimed:usb.util.release_interface(dev,4)
            if detached:dev.attach_kernel_driver(4)
            if dev is not None:usb.util.dispose_resources(dev)
        finally:(a.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
if __name__=='__main__':main()
