#!/usr/bin/env python3
"""Offline recovery through an ISOLATED ZB25VQ32 and a MicroPython Pico.

LAST RESORT, NOT HARDWARE-VALIDATED (the read-only dump_flash_rp2040.py is). Never run on a chip still driven by the camera.
Accepts the stock firmware or your own complete 4-MiB backup (stock boot header).
Reads the current chip twice before writes, journals intent, restores only
changed sectors, and verifies the entire chip afterward. Never retries errors.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import serial
import time
import zlib
from dump_flash_rp2040 import enter_raw_repl, send_program, read_until

ROOT = Path(__file__).resolve().parents[2]
STOCK_SHA = 'e22557a4497a1199c9ecc3956b18a89ac70af800b674055513f3de91bfb8f224'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def validate_backup(data):
    """The stock firmware (flash_zb25vq32_read1.bin) or your own complete backup
    (backup.bin / before.bin from penguin_flash.py): 4 MiB with the stock boot header."""
    stock = (ROOT/'flash_zb25vq32_read1.bin').read_bytes()
    if sha(stock) != STOCK_SHA: raise ValueError('stock firmware file changed')
    if len(data) != 0x400000 or data[:0x3000] != stock[:0x3000]:
        raise ValueError('Only a complete 4 MiB backup with the stock boot header may be restored')


def journal(out, event, **data):
    with (out / 'journal.jsonl').open('a') as f:
        f.write(json.dumps({'event': event, **data}) + '\n')
        f.flush(); os.fsync(f.fileno())


def command(port, message):
    data = (message + '\n').encode('ascii')
    if port.write(data) != len(data):
        raise RuntimeError('Short serial command; completion uncertain, no retry')
    response = port.readline()
    if not response.endswith(b'\n'):
        raise RuntimeError('Missing complete response; no retry')
    response = response.rstrip(b'\r\n').decode('ascii')
    if response.startswith('ERROR:'):
        raise RuntimeError(response)
    return response


def read_image(port, out, name):
    result = bytearray()
    with (out / name).open('xb') as f:
        for address in range(0, 0x400000, 4096):
            response = command(port, f'READ:{address:06x}')
            fields = response.split(':')
            if len(fields) != 3 or fields[:2] != ['DATA', f'{address:06x}'] or len(fields[2]) != 8192:
                raise RuntimeError('Bad read address/length')
            block = bytes.fromhex(fields[2])
            if len(block) != 4096:
                raise RuntimeError('Bad read data')
            f.write(block); result.extend(block)
            if (address + 4096) % 0x80000 == 0:
                print(f'{name}: {(address + 4096)//1024}/4096 KiB', flush=True)
        f.flush(); os.fsync(f.fileno())
    return bytes(result)


def restore(port, backup, out):
    validate_backup(backup)
    first = read_image(port, out, 'before-1.bin')
    second = read_image(port, out, 'before-2.bin')
    if first != second:
        raise RuntimeError('Two current reads disagree; no writes allowed')
    changed = [off for off in range(0, 0x400000, 4096) if first[off:off+4096] != backup[off:off+4096]]
    journal(out, 'plan', before_sha256=sha(first), restore_sha256=sha(backup), changed_sectors=changed)
    # Restore boot-critical sectors last. This is not an atomic update: recovery
    # must remain physically available until the complete image is verified.
    changed.sort(key=lambda off: (off < 0x2400 or off == 0x19000, off))
    for off in changed:
        old, new = first[off:off+4096], backup[off:off+4096]
        old_crc, new_crc = zlib.crc32(old), zlib.crc32(new)
        journal(out, 'restore_intent', address=off, before_sha256=sha(old), after_sha256=sha(new))
        response = command(port, f'RESTORE:{off:06x}:{old_crc:08x}:{new_crc:08x}:{new.hex()}')
        if response != f'RESTORED:{off:06x}:{new_crc:08x}':
            raise RuntimeError('Unknown restore completion; no retry')
        journal(out, 'sector_verified', address=off)
    final = read_image(port, out, 'after.bin')
    if final != backup:
        raise RuntimeError('Full image verification failed; no automatic retry')
    journal(out, 'complete', restored_sha256=sha(final), changed_sectors=len(changed))
    return len(changed)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('port'); p.add_argument('backup', type=Path)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--restore-isolated-flash', action='store_true')
    args = p.parse_args()
    if not args.restore_isolated_flash:
        p.error('Requires --restore-isolated-flash; camera must NOT drive the chip')
    if args.output.exists():
        p.error('Output exists; never retry an uncertain attempt')
    backup = args.backup.read_bytes(); validate_backup(backup)
    driver = (Path(__file__).resolve().parent / 'pico_flash_recovery.py').read_text()
    source = driver + '''
import sys, ubinascii, time
from machine import Pin, SPI
cs = Pin(17, Pin.OUT, value=1)
spi = SPI(0, baudrate=2000000, polarity=0, phase=0,
          sck=Pin(18), mosi=Pin(19), miso=Pin(16))
flash = Flash(spi, cs, time.ticks_ms, time.ticks_diff, time.sleep_ms)
serve(flash, sys.stdin, sys.stdout, ubinascii.hexlify, ubinascii.unhexlify, ubinascii.crc32)
'''
    args.output.mkdir(parents=True)
    try:
        with serial.Serial(args.port,115200,timeout=20,write_timeout=20) as port:
            time.sleep(1); enter_raw_repl(port); send_program(port,source)
            read_until(port,b'OK',1000)
            if port.readline().strip() != b'READY:5e4016':
                raise RuntimeError('Pico recovery driver/flash identity mismatch')
            count = restore(port, backup, args.output)
            if command(port,'QUIT') != 'DONE':
                raise RuntimeError('Driver exit not acknowledged; image verification already completed')
            print(f'Restored and verified {sha(backup)}; {count} sectors changed.')
    except Exception as exc:
        journal(args.output,'stopped',error=str(exc))
        raise


if __name__ == '__main__': main()
