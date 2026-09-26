#!/usr/bin/env python3
"""Read-only check of a running camera after an install + restart (update 32).

Reads the application RAM (2 MiB) over the normal USB mode, finds the loaded
payload, and verifies: every hook word, the stock application text, the loader's
relocation/hook tables and the WHOLE static payload body (only the regions in
payload_layout.RUNTIME may differ). No uploads, allocations, resets or writes.
"""
import argparse
import json
import struct
from pathlib import Path
from persistent_payload import unpack, relocate, relocate_hooks, sha
from persistent_penguins import SIZE
from payload_layout import runtime_mask, RUNTIME

ROOT = Path(__file__).resolve().parents[1]


def check(snapshot, candidate, payload):
    """snapshot: 2 MiB RAM from 0x02000000; candidate: 4 MiB flash image;
    payload: the PGFX bundle inside it. Raises on any mismatch."""
    if len(snapshot) != 0x200000: raise ValueError('wrong snapshot size')
    if candidate[0x200000:0x200000+len(payload)] != payload: raise ValueError('payload is not the one in this image')
    blob, relocs, hooks = unpack(payload, expected_size=SIZE)
    positions = [p for p in range(0x90000, len(snapshot)-len(payload), 4)
                 if (p+32) % 64 == 0 and snapshot[p:p+32] == payload[:32]]
    if len(positions) != 1: raise ValueError('expected exactly one loaded payload header')
    offset = positions[0]+32; base = 0x02000000+offset
    expected = bytearray(candidate[0x2400:0x68b00])
    for address, old, new in relocate_hooks(hooks, base, SIZE):
        if struct.unpack_from('<I', snapshot, address-0x02000000)[0] != new:
            raise ValueError(f'incorrect live hook at {address:#x}')
        index = address-0x02000000
        if 0 <= index < len(expected): struct.pack_into('<I', expected, index, new)
    if snapshot[:len(expected)] != expected: raise ValueError('unexplained application code difference')
    body = relocate(blob, relocs, base, SIZE)
    live = snapshot[offset:offset+SIZE]; mask = runtime_mask()
    bad = [i for i in range(SIZE) if not mask[i] and live[i] != body[i]]
    if bad: raise ValueError(f'{len(bad)} static payload bytes differ, first at {bad[0]:#x}')
    table = 32+SIZE
    if snapshot[positions[0]+table:positions[0]+len(payload)] != payload[table:]:
        raise ValueError('loaded relocation/hook tables differ')
    return dict(candidate_sha256=sha(candidate), payload_sha256=sha(payload), snapshot_sha256=sha(snapshot),
                payload_base=hex(base), hook_count=len(hooks), all_hooks_verified=True,
                application_text_matches=True, static_payload_bytes_verified=SIZE-sum(mask),
                runtime_regions_skipped=[(hex(lo), hex(hi)) for lo, hi, _ in RUNTIME],
                active_mode=struct.unpack_from('<I', snapshot, 0x85e9c)[0])


def read_ram(output):
    import usb.core, usb.util
    from camera_usb import read_mem, BIAS
    ds = list(usb.core.find(find_all=True, idVendor=0x1908, idProduct=0x3283))
    if len(ds) != 1: raise RuntimeError('expected exactly one camera in normal USB mode')
    dev = ds[0]; claimed = detached = False; certain = True; tag = 18000
    try:
        intf = usb.util.find_descriptor(dev.get_active_configuration(), bInterfaceNumber=4, bAlternateSetting=0)
        if intf is None or (intf.bInterfaceClass, intf.bInterfaceSubClass, intf.bInterfaceProtocol) != (8, 6, 80):
            raise RuntimeError('unexpected USB interface')
        if dev.is_kernel_driver_active(4): dev.detach_kernel_driver(4); detached = True
        usb.util.claim_interface(dev, 4); claimed = True
        original = (ROOT/'flash_zb25vq32_read1.bin').read_bytes()
        certain = False
        if read_mem(dev, tag, BIAS+0x4341c, 304) != original[0x4341c:0x4354c]: raise RuntimeError('unexpected read helper')
        certain = True; raw = bytearray()
        for off in range(0, 0x200000, 4096):
            tag += 1; certain = False
            raw.extend(read_mem(dev, tag, 0x02000000+off, 4096)); certain = True
        (output/'ram.bin').write_bytes(raw)
        return bytes(raw)
    finally:
        if claimed: usb.util.release_interface(dev, 4)
        if detached and certain: dev.attach_kernel_driver(4)
        usb.util.dispose_resources(dev)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--image', type=Path, required=True, help='the installed 4 MiB image')
    p.add_argument('--payload', type=Path, required=True, help='its native-effects-menu.pgfx')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.output.exists(): p.error('new output directory required')
    args.output.mkdir(parents=True)
    report = check(read_ram(args.output), args.image.read_bytes(), args.payload.read_bytes())
    (args.output/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__': main()
