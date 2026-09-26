#!/usr/bin/env python3
"""Read a ZB25VQ32 through an RP2040 running MicroPython (read-only)."""

import argparse
import hashlib
import os
import re
import sys
import time
from pathlib import Path

import serial


FLASH_SIZE = 4 * 1024 * 1024
EXPECTED_ID = "5e4016"
EXPECTED_PREFIX = bytes.fromhex("00 00 02 00 42 4c 44 52")


MICROPYTHON_PROGRAM = r'''
import sys
import ubinascii
from machine import Pin, SPI

cs = Pin(17, Pin.OUT, value=1)
spi = SPI(0, baudrate=2000000, polarity=0, phase=0,
          sck=Pin(18), mosi=Pin(19), miso=Pin(16))

def read_id():
    cs(0)
    try:
        spi.write(b'\x9f')
        return spi.read(3)
    finally:
        cs(1)

def read_flash(address, buffer):
    command = bytes((3, address >> 16, (address >> 8) & 255, address & 255))
    cs(0)
    try:
        spi.write(command)
        spi.readinto(buffer, 255)
    finally:
        cs(1)

def main():
    chip_id = read_id()
    sys.stdout.write('BEGIN:' + ubinascii.hexlify(chip_id).decode() + '\n')
    if chip_id != b'\x5e\x40\x16':
        sys.stdout.write('ERROR:Unexpected JEDEC ID\n')
        return
    buffer = bytearray(256)
    for address in range(0, 4194304, 256):
        read_flash(address, buffer)
        sys.stdout.write('@%06x:%s\n' %
                         (address, ubinascii.hexlify(buffer).decode()))
    sys.stdout.write('END\n')

main()
'''


def read_until(port, marker, max_bytes=10000):
    seen = bytearray()
    while marker not in seen:
        byte = port.read(1)
        if not byte:
            raise TimeoutError(f"Timed out waiting for {marker!r}; got {seen[-150:]!r}")
        seen.extend(byte)
        if len(seen) > max_bytes:
            raise RuntimeError(f"No {marker!r} in first {max_bytes} bytes")
    return bytes(seen)


def enter_raw_repl(port):
    port.write(b"\x03\x03")  # Stop a previous MicroPython program.
    time.sleep(0.2)
    port.reset_input_buffer()
    port.write(b"\x01")  # Ctrl-A: raw REPL.
    greeting = read_until(port, b">", 2048)
    if b"raw REPL" not in greeting:
        raise RuntimeError(f"USB device is not at a MicroPython raw REPL: {greeting!r}")


def send_program(port, source):
    # Pace the source: older MicroPython raw REPLs have a small USB receive queue.
    payload = source.encode()
    for start in range(0, len(payload), 64):
        port.write(payload[start:start + 64])
        time.sleep(0.01)
    port.write(b"\x04")  # Execute the program.


def dump(port_name, output):
    if output.exists() or output.with_suffix(output.suffix + ".part").exists():
        raise FileExistsError(f"Refusing to overwrite {output} or its .part file")
    partial = output.with_suffix(output.suffix + ".part")
    digest = hashlib.sha256()
    received = 0
    with serial.Serial(port_name, 115200, timeout=20, write_timeout=20) as port:
        time.sleep(1)
        enter_raw_repl(port)
        send_program(port, MICROPYTHON_PROGRAM)
        read_until(port, b"OK", 1000)
        with partial.open("xb") as image:
            while True:
                line = port.readline().strip()
                if not line:
                    raise TimeoutError(f"Transfer stopped after {received} bytes")
                if line.startswith(b"BEGIN:"):
                    chip_id = line[6:].decode().lower()
                    if chip_id != EXPECTED_ID:
                        raise RuntimeError(f"Unexpected JEDEC ID: {chip_id}")
                    print(f"JEDEC ID: {chip_id}", flush=True)
                elif line == b"END":
                    break
                elif line.startswith(b"ERROR:"):
                    raise RuntimeError(line.decode())
                elif line.startswith(b"@"):
                    match = re.fullmatch(rb"@([0-9a-f]{6}):([0-9a-f]{512})", line)
                    if not match:
                        raise RuntimeError(f"Malformed data line: {line[:100]!r}")
                    address = int(match.group(1), 16)
                    if address != received:
                        raise RuntimeError(f"Expected address {received:06x}, got {address:06x}")
                    block = bytes.fromhex(match.group(2).decode())
                    if received == 0 and not block.startswith(EXPECTED_PREFIX):
                        raise RuntimeError(f"First bytes changed: {block[:16].hex(' ')}")
                    image.write(block)
                    digest.update(block)
                    received += len(block)
                    if received % (256 * 1024) == 0:
                        print(f"{received // 1024} / {FLASH_SIZE // 1024} KiB", flush=True)
                else:
                    raise RuntimeError(f"Unexpected response: {line[:100]!r}")
        port.write(b"\x02")  # Ctrl-B: return to friendly REPL.
    if received != FLASH_SIZE:
        raise RuntimeError(f"Incomplete image: {received} of {FLASH_SIZE} bytes")
    os.rename(partial, output)
    print(f"Saved {output} ({received} bytes), SHA-256 {digest.hexdigest()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port", help="MicroPython USB serial port, e.g. /dev/ttyACM0")
    parser.add_argument("output", type=Path, help="New dump file; never overwritten")
    args = parser.parse_args()
    try:
        dump(args.port, args.output)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
