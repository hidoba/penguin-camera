#!/usr/bin/env python3
"""Prove SDK RAM allocation, guarded SPI reads and write refusal without an SD card.
No flash erase/program call is allowed to reach the SPI driver. No reset or printing.
Requires completed read-only post-restart boot verification with user-confirmed SD absence.
"""
import argparse,json
from pathlib import Path
from mk1_usb_flash import Session,CHUNK,SECTOR,STOCK_SHA,sha,journal
from mk1_fast_flash import Operations
import build_mk1_effects as b

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--boot-check',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    prior=json.loads((a.boot_check/'report.json').read_text());image,m=b.build()
    if not prior.get('completed') or not prior.get('user_confirmed_SD_absent') or prior['candidate_sha256']!=sha(image):
        raise RuntimeError('exact post-restart no-SD boot verification required')
    stock=(b.ROOT/'flash_read1.bin').read_bytes()
    if sha(stock)!=STOCK_SHA: raise RuntimeError('stock hash mismatch')
    a.output.mkdir(parents=True,exist_ok=False);s=Session(a.output,stock)
    report=dict(completed=False,flash_written=False,candidate_sha256=sha(image),SD_absent_confirmed=True,
        tested_flash_sectors=[],write_guards_refused_boot_sector=False)
    try:
        s.open();s.write_packet=512;readbuf=s.allocate(CHUNK+128);source=s.allocate(SECTOR+128)
        ops=Operations(s,readbuf,source,[0x23000,0x78000,0x79000,0x7a000,0x7b000])
        for name in ('erase','program'):ops.refuse(name)
        report['write_guards_refused_boot_sector']=True
        for off in (0,0x23000,0x78000,0x79000,0x7a000,0x7b000):
            first=ops.read(off,SECTOR,0x26);second=ops.read(off,SECTOR,0xc9)
            if first!=second or first!=image[off:off+SECTOR]:raise RuntimeError(f'no-SD SPI read mismatch at {off:#x}')
            report['tested_flash_sectors'].append(off)
        report.update(completed=True,SDK_allocations_and_native_calls_verified=True,USB_interface=4)
        journal(a.output,'no_SD_access_verified')
    except Exception as exc:
        report['error']=str(exc);journal(a.output,'stopped',error=str(exc));raise
    finally:
        try:s.close()
        finally:(a.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
if __name__=='__main__':main()
