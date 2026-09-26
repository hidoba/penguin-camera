#!/usr/bin/env python3
"""Install / update / uninstall the penguin-camera firmware over USB (no soldering).

    python3 tools/penguin_flash.py backup  --output DIR
    python3 tools/penguin_flash.py install --image NEW.bin [--known OLD.bin ...] --output DIR
    python3 tools/penguin_flash.py restart --output DIR
    python3 tools/penguin_flash.py check   --image NEW.bin --output DIR
    python3 tools/penguin_flash.py verify  --image NEW.bin --output DIR

The camera must be switched on and connected over USB in its normal mode (it
shows up as USB id 1908:3283). Every command needs a NEW output directory; it
keeps a journal, both full flash reads (your backup) and a report.

install:
  1. Two full 4 MiB flash reads must be identical (saved as before.bin).
  2. The flash must be a KNOWN image: the stock firmware (firmware/original) or
     one given with --known (e.g. the release you are updating from). Your own
     data is exempt and always preserved: settings sector 0x1d7000 and the
     internal-photo area 0x1d8000..0x200000. The boot header (first 0x3000
     bytes) may never change.
  3. Only sectors that differ are written, each through a private RAM bridge that
     can erase/program exactly that sector, checked before the write and read back
     twice after it. Installing: the flash-guard sector first, the loader sector
     0x19000 last, after the whole staged flash was verified and the new loader
     was rehearsed in RAM. Uninstalling (--image = stock): loader sector first.
  4. The whole 4 MiB are read back and must equal the target.
No retries. If anything fails after writing started: KEEP THE CAMERA POWERED and
read the journal; do not rerun blindly.
"""
import argparse
import json
import os
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

SIZE = 0x400000
SECTOR = 4096
BOOT = slice(0, 0x3000)
USER = slice(0x1d7000, 0x200000)          # settings + internal photos: never written
ACTIVATION, GUARDS = 0x19000, 0x39000
PAYLOAD_AT = 0x200000


def sha(data):
    import hashlib
    return hashlib.sha256(data).hexdigest()


def original_image():
    return (ROOT/'flash_zb25vq32_read1.bin').read_bytes()


def payload_of(image):
    """The PGFX payload inside an image, or None for a stock image."""
    from persistent_payload import HEADER
    if image[PAYLOAD_AT:PAYLOAD_AT+4] != b'PGFX': return None
    fields = HEADER.unpack_from(image, PAYLOAD_AT)
    return bytes(image[PAYLOAD_AT:PAYLOAD_AT+HEADER.size+fields[6]])


class Plan:
    """Pure policy (no I/O): which sectors change, in which order."""
    def __init__(self, current, target, known):
        if len(current) != SIZE or len(target) != SIZE: raise ValueError('images must be 4 MiB')
        original = original_image()
        self.source = None
        for name, image in known:
            if len(image) != SIZE: continue
            normalized = bytearray(current); normalized[USER] = image[USER]
            if bytes(normalized) == image: self.source = name; break
        if self.source is None:
            raise ValueError('Camera flash is not a known image (stock or --known); nothing written')
        if target[BOOT] != current[BOOT] or target[BOOT] != original[BOOT]:
            raise ValueError('The boot header must stay identical')
        wanted = bytearray(target); wanted[USER] = current[USER]
        self.current, self.target = bytes(current), bytes(wanted)
        self.changed = tuple(a for a in range(0, SIZE, SECTOR) if current[a:a+SECTOR] != self.target[a:a+SECTOR])
        installs_loader = self.target[ACTIVATION:ACTIVATION+SECTOR] != original[ACTIVATION:ACTIVATION+SECTOR]
        self.payload = payload_of(self.target) if installs_loader else None
        if installs_loader and self.payload is None: raise ValueError('target has a loader but no payload')
        data = [a for a in self.changed if a not in (ACTIVATION, GUARDS)]
        upper = [a for a in data if a >= 0x200000]; lower = [a for a in data if a < 0x200000]
        if installs_loader:
            order = ([GUARDS] if GUARDS in self.changed else []) + upper + lower
            self.last = ACTIVATION if ACTIVATION in self.changed else None
        else:      # uninstall / stock target: disable the extension first, guards last
            order = ([ACTIVATION] if ACTIVATION in self.changed else []) + lower + upper
            self.last = GUARDS if GUARDS in self.changed else None
        self.order = tuple(order) + ((self.last,) if self.last is not None else ())
        if sorted(self.order) != sorted(self.changed): raise ValueError('internal: bad sector order')
        if any(USER.start <= a < USER.stop for a in self.order): raise ValueError('internal: user data selected')

    def sector(self, address):
        if address not in self.changed: raise ValueError('sector not in this plan')
        return self.current[address:address+SECTOR], self.target[address:address+SECTOR]

    def require_transition(self, address, before, after):
        if (bytes(before), bytes(after)) != self.sector(address): raise ValueError('not the planned transition')

    def staged(self):
        """Flash expected right before the last sector is written."""
        data = bytearray(self.target)
        if self.last is not None: data[self.last:self.last+SECTOR] = self.current[self.last:self.last+SECTOR]
        return bytes(data)


def durable(path, data):
    with path.open('xb') as f:
        f.write(data); f.flush(); os.fsync(f.fileno())


def live_image(s, known):
    """Which known image is running (application helpers compared in RAM)."""
    from camera_usb import BIAS
    ranges = ((0x39334, 0x96c), (0x2c438, 0x268), (0x1996c, 0x3ec))
    for name, image in known:
        if all(s.read(BIAS+off, n) == image[off:off+n] for off, n in ranges): return name, image
    raise RuntimeError('The running firmware is not a known image; nothing written')


def open_session(out, known):
    from camera_usb import Session, heap, BIAS
    s = Session(original_image(), out)
    s.open()
    name, image = live_image(s, known)
    ranges = ((0x39334, 0x96c), (0x2c438, 0x268), (0x1996c, 0x3ec))
    def check_live():
        s.idle()
        for off, n in ranges:
            if s.read(BIAS+off, n) != image[off:off+n]: raise RuntimeError('live firmware changed; stop')
    check_live()
    for record in heap(s.read):
        if record['used']: s.frame_ranges.append((record['pointer'], record['size']))
    if s.invoke(BIAS+0x39398, []) != 0x5e4016: raise RuntimeError('unexpected flash chip ID')
    return s, name, check_live


def two_reads(s, out):
    from camera_usb import snapshot, CHUNK
    base = s.allocate(CHUNK+128)
    first = snapshot(s, base, 0, out); second = snapshot(s, base, 1, out)
    if first != second: raise RuntimeError('the two full flash reads differ; nothing written')
    return base, first


def known_images(paths):
    known = [('stock', original_image())]
    for p in paths or ():
        known.append((str(p), Path(p).read_bytes()))
    return known


def cmd_backup(args):
    args.output.mkdir(parents=True)
    s, name, check_live = open_session(args.output, known_images(args.known))
    try:
        _, flash = two_reads(s, args.output); check_live()
        durable(args.output/'backup.bin', flash)
        report = dict(running=name, backup_sha256=sha(flash), reads_identical=True, flash_written=False)
        (args.output/'report.json').write_text(json.dumps(report, indent=2)+'\n'); print(json.dumps(report, indent=2))
    finally:
        s.close()


def write_sector(s, base, bridge, plan, address, out):
    from camera_usb import BIAS, journal, BEFORE, AFTER, read_flash, sector_bridge, ERASE, PROGRAM, BRIDGE_SIZE
    before, after = plan.sector(address)
    plan.require_transition(address, before, after)
    if read_flash(s, base, address, SECTOR, 0x71) != before: raise RuntimeError('sector changed before write; stop')
    code = sector_bridge(original_image(), bridge, address, base+64, allowed=plan.changed)
    s.write(bridge, code); s.call(BIAS+0x2974c)
    journal(out, 'sector_intent', address=address, before_sha256=sha(before), after_sha256=sha(after))
    if before != b'\xff'*SECTOR:
        journal(out, 'erase_intent', address=address)
        status = s.invoke(bridge+ERASE, [address]); journal(out, 'erase_return', address=address, status=status)
        if status: raise RuntimeError('erase failed')
        if read_flash(s, base, address, SECTOR, 0x26) != b'\xff'*SECTOR: raise RuntimeError('erase readback failed')
    raw = BEFORE+after+AFTER
    s.write(base, raw); s.invoke(BIAS+0x29790, [base, len(raw)])
    journal(out, 'program_intent', address=address)
    status = s.invoke(bridge+PROGRAM, [address, base+64, SECTOR]); journal(out, 'program_return', address=address, status=status)
    if status or s.read(base, len(raw)) != raw or s.read(bridge, BRIDGE_SIZE) != code: raise RuntimeError('program guards failed')
    for poison in (0x36, 0xc9):
        if read_flash(s, base, address, SECTOR, poison) != after: raise RuntimeError('program readback failed')
    journal(out, 'sector_verified', address=address)


def cmd_install(args):
    import usb.util
    from camera_usb import (BIAS, journal, snapshot, rehearse, PROBE_BYTES, sector_bridge,
                            ERASE, PAGE, PROGRAM, BRIDGE_SIZE)
    target = args.image.read_bytes(); known = known_images(args.known)
    args.output.mkdir(parents=True)
    report = dict(installed=False, mutation_started=False, target_sha256=sha(target))
    s, running, check_live = open_session(args.output, known)
    try:
        base, current = two_reads(s, args.output)
        durable(args.output/'before.bin', current)
        plan = Plan(current, target, known)
        if plan.source != running: raise RuntimeError(f'flash is {plan.source} but {running} is running; restart first')
        durable(args.output/'target.bin', plan.target)
        report.update(source=plan.source, before_sha256=sha(current), final_sha256=sha(plan.target),
                      order=[hex(a) for a in plan.order], sectors=len(plan.order))
        print(f'Camera runs {plan.source}; {len(plan.order)} sectors to write.', flush=True)
        if not plan.order:
            report['installed'] = True; print('Already installed.'); return
        if plan.payload is not None:
            from persistent_payload import unpack
            from persistent_penguins import SIZE as PAYLOAD_SIZE
            unpack(plan.payload, expected_size=PAYLOAD_SIZE)
        bridge = s.allocate(BRIDGE_SIZE); probe = s.allocate(PROBE_BYTES)
        first = plan.order[0]
        s.write(bridge, sector_bridge(original_image(), bridge, first, base+64, allowed=plan.changed)); s.call(BIAS+0x2974c)
        for entry, call_args in ((ERASE, [0]), (PAGE, [0, base+64]), (PAGE, [first, base+128]), (PROGRAM, [first, base+64, 256])):
            if s.invoke(bridge+entry, call_args) != 0xffffffff:
                s.certain = False; raise RuntimeError('private bridge refusal check failed')
        check_live(); journal(args.output, 'preflight_passed', plan=report['order'])
        report['mutation_started'] = True
        for n, address in enumerate(plan.order[:-1] if plan.last is not None else plan.order, 1):
            write_sector(s, base, bridge, plan, address, args.output)
            print(f'  sector {address:#08x} verified ({n}/{len(plan.order)})', flush=True)
        if plan.last is not None:
            staged = snapshot(s, base, 1, args.output, total_passes=3)
            if staged != plan.staged(): raise RuntimeError('staged flash mismatch; final sector NOT written')
            journal(args.output, 'whole_staged_image_verified', sha256=sha(staged))
            if plan.payload is not None:
                check_live()
                rehearse(s, probe, plan.payload, args.output, mismatch=True)
                rehearse(s, probe, plan.payload, args.output)
                check_live()
            write_sector(s, base, bridge, plan, plan.last, args.output)
            print(f'  sector {plan.last:#08x} verified (last)', flush=True)
        final = snapshot(s, base, 2, args.output, total_passes=3)
        if final != plan.target: raise RuntimeError('final full flash mismatch')
        journal(args.output, 'whole_final_image_verified', sha256=sha(final))
        check_live()
        report['installed'] = True
        print('Installed and verified. Restart the camera (penguin_flash.py restart, or switch it off and on).')
    except Exception as exc:
        report['error'] = str(exc)
        if report['mutation_started']: s.certain = False
        journal(args.output, 'stopped', error=str(exc), transport_certain=s.certain)
        raise
    finally:
        (args.output/'report.json').write_text(json.dumps(report, indent=2)+'\n')
        if not s.certain:
            print('KEEP THE CAMERA POWERED. Do not retry; inspect the journal.', flush=True)
            if s.claimed: usb.util.release_interface(s.dev, 4)
            if s.dev is not None: usb.util.dispose_resources(s.dev)
        else:
            s.close()


def wrapper(tag, cdb, length=0, direction=0):
    """USB mass-storage command block wrapper (same as tools/usb_bootloader_probe.py)."""
    return struct.pack('<4sIIBBB16s', b'USBC', tag, length, direction, 0, len(cdb), cdb)


def check_status(raw, tag):
    if bytes(raw) != struct.pack('<4sIIB', b'USBS', tag, 0, 0): raise RuntimeError('unexpected USB status')


def cmd_restart(args):
    """One DA (ROM bootloader) entry command; the ROM's idle watchdog restarts the
    camera into its firmware a few seconds later."""
    import usb.core
    from camera_usb import Session, BIAS
    args.output.mkdir(parents=True)
    if list(usb.core.find(find_all=True, idVendor=0x1908, idProduct=0x3319)): raise RuntimeError('already in ROM mode')
    s = Session(original_image(), args.output); s.open()
    try:
        if s.read(BIAS+0x43874, 0x290) != original_image()[0x43874:0x43b04]: raise RuntimeError('entry routine changed')
        s.idle(); s.backup = None
        print('Restarting the camera (one ROM-entry command)...', flush=True)
        try:
            s.dev.write(1, wrapper(8100, b'\xda'+b'\0'*15), timeout=2000)
            check_status(s.dev.read(0x81, 13, timeout=2500), 8100)
        except usb.core.USBError:
            pass
    finally:
        s.backup = None; s.detached = False
        try: s.close()
        except usb.core.USBError: pass
    deadline = time.monotonic()+60
    while time.monotonic() < deadline:
        time.sleep(2)
        if list(usb.core.find(find_all=True, idVendor=0x1908, idProduct=0x3283)):
            print('Camera is back.'); time.sleep(3); return
    raise RuntimeError('camera did not come back over USB within 60 s (it may still be restarting)')


def cmd_verify(args):
    """Read-only: two full flash reads must be identical and equal --image, except the
    user settings/photo area. Exits non-zero on any difference."""
    args.output.mkdir(parents=True)
    image = args.image.read_bytes()
    s, name, check_live = open_session(args.output, known_images([args.image]))
    try:
        _, flash = two_reads(s, args.output); check_live()
    finally:
        s.close()
    normalized = bytearray(flash); normalized[USER] = image[USER]
    differing = [hex(a) for a in range(0, SIZE, SECTOR) if normalized[a:a+SECTOR] != image[a:a+SECTOR]]
    report = dict(running=name, flash_sha256=sha(flash), image_sha256=sha(image), matches_image=not differing,
                  differing_sectors=differing, flash_written=False)
    (args.output/'report.json').write_text(json.dumps(report, indent=2)+'\n'); print(json.dumps(report, indent=2))
    if differing: raise SystemExit(1)


def cmd_check(args):
    from firmware_boot_check import check, read_ram
    args.output.mkdir(parents=True)
    image = args.image.read_bytes(); payload = payload_of(image)
    if payload is None: raise SystemExit('stock image: nothing to check')
    report = check(read_ram(args.output), image, payload)
    (args.output/'report.json').write_text(json.dumps(report, indent=2)+'\n'); print(json.dumps(report, indent=2))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='command', required=True)
    for name in ('backup', 'install', 'restart', 'check', 'verify'):
        q = sub.add_parser(name); q.add_argument('--output', type=Path, required=True)
        if name in ('install', 'check', 'verify'): q.add_argument('--image', type=Path, required=True)
        if name in ('install', 'backup'): q.add_argument('--known', type=Path, action='append', help='another image the camera may be running')
    args = p.parse_args()
    if args.output.exists(): p.error('use a NEW output directory (never retry into an old one)')
    {'backup': cmd_backup, 'install': cmd_install, 'restart': cmd_restart, 'check': cmd_check,
     'verify': cmd_verify}[args.command](args)


if __name__ == '__main__': main()
