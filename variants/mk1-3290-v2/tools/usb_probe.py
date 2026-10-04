#!/usr/bin/env python3
"""READ-ONLY probe of the MK1-3290-V2 camera (AX3291A) over its normal USB mode.

Uses the stock vendor SCSI command 0xCD (same SDK as the penguin camera's AX3295B)
to read application RAM and compare the USB/flash helper code with the Pico dump.
It never writes RAM, never uses the callback field, never resets the device.

    python3 tools/usb_probe.py                 # compare helpers, print verdict
    python3 tools/usb_probe.py --ram-dump out  # also save 0x02000000..0x02800000
"""
import argparse
import hashlib
import struct
import sys
from pathlib import Path

import usb.core
import usb.util

ROOT = Path(__file__).resolve().parents[1]
DUMP = ROOT / 'flash_read1.bin'
DUMP_SHA = '33ac2db5716dacf06b60f97c5efbb2b2b77d820fe2b13e58c1eeb4379094fab5'
BIAS = 0x02000000 - 0x2600          # application RAM = flash offset + BIAS
IDS = ((0x1908, 0x3283), (0x0219, 0x3280))  # 0219:3280 = 'GENERAL - AUDIO', MSC-only (descriptor at flash 0x7e984)
INTF = 4
MEM_WRAPPER = 0x0204A9D4            # flash 0x4cfd4; penguin used 0x0204109C
RAM_END = 0x02800000                # BSS clear/heap end 0x027fec00 -> 8 MiB

# (name, flash offset, length): code compared against the dump after boot
CHECKS = [
    ('usb mem read/write/wrapper/call', 0x4cedc, 0x184),
    ('usb scsi dispatcher + DA entry', 0x4d3ac, 0x400),
    ('spi flash driver', 0x3eb08, 0x400),
    ('erase 4k', 0x3ee90, 0x80),
    ('cache maintenance', 0x2c978, 0x100),
    ('malloc/free', 0x3fed0, 0x120),
]


def read_mem(dev, tag, address, size):
    cdb = b'\xcd' + struct.pack('<III', MEM_WRAPPER, address, 0xFFFFFFFF) + b'\0' * 3
    cbw = struct.pack('<4sIIBBB16s', b'USBC', tag, size, 0x80, 0, 16, cdb)
    if dev.write(0x01, cbw, timeout=2000) != 31:
        raise RuntimeError('short command write; not retrying')
    data = bytes(dev.read(0x81, size, timeout=2000))
    csw = bytes(dev.read(0x81, 13, timeout=2000))
    if len(csw) != 13 or struct.unpack('<4sIIB', csw) != (b'USBS', tag, 0, 0):
        raise RuntimeError(f'unexpected CSW {csw.hex()}')
    if len(data) != size:
        raise RuntimeError('short data transfer')
    return data


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--ram-dump', type=Path, help='new file for a full 8 MiB RAM read')
    args = ap.parse_args()
    flash = DUMP.read_bytes()
    if hashlib.sha256(flash).hexdigest() != DUMP_SHA:
        sys.exit('flash_read1.bin hash mismatch')
    if args.ram_dump and args.ram_dump.exists():
        sys.exit(f'refusing to overwrite {args.ram_dump}')

    devices = [d for v, p in IDS for d in usb.core.find(find_all=True, idVendor=v, idProduct=p)]
    if len(devices) != 1:
        sys.exit('expected exactly one camera on USB (1908:3283 or 0219:3280); is it on and connected?')
    dev = devices[0]
    print(f'device {dev.idVendor:04x}:{dev.idProduct:04x} bus {dev.bus} address {dev.address}')
    intf = usb.util.find_descriptor(dev.get_active_configuration(), bInterfaceNumber=INTF, bAlternateSetting=0)
    if intf is None or (intf.bInterfaceClass, intf.bInterfaceSubClass, intf.bInterfaceProtocol) != (8, 6, 80):
        sys.exit('interface 4 is not the expected mass-storage interface')
    detached = False
    if dev.is_kernel_driver_active(INTF):
        dev.detach_kernel_driver(INTF)
        detached = True
    usb.util.claim_interface(dev, INTF)
    tag, ok = 3000, True
    try:
        for name, off, n in CHECKS:
            tag += 1
            got = read_mem(dev, tag, off + BIAS, n)
            same = got == flash[off:off + n]
            ok &= same
            print(f'{"OK  " if same else "DIFF"} {name:34s} RAM {off + BIAS:#010x} ({n} bytes)')
        if args.ram_dump:
            out = bytearray()
            for a in range(0x02000000, RAM_END, 4096):
                tag += 1
                out += read_mem(dev, tag, a, 4096)
            args.ram_dump.write_bytes(out)
            print(f'saved {args.ram_dump}, sha256 {hashlib.sha256(out).hexdigest()}')
    finally:
        usb.util.release_interface(dev, INTF)
        if detached:
            dev.attach_kernel_driver(INTF)
    print('VERDICT:', 'USB RAM access works and helpers match the dump' if ok else 'helper mismatch - stop and investigate')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
