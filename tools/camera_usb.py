"""USB access to the camera's normal (stock) USB mode, for installing firmware.

Collected unchanged in behaviour from the hardware-proven development tools
(usb_read_probe, usb_display_test, usb_print_calibration, usb_install_gray_ram,
usb_jpeg_probe.Session, usb_combined_menu_ram.invoke, usb_flash_sector_test,
usb_flash_snapshot, usb_retire_capture_bundle.heap, usb_loader_rehearsal), with
their research-only features removed.

The camera's stock firmware exposes vendor SCSI command 0xCD on USB interface 4:
read RAM, write RAM (+ optional callback) and call a RAM address. With these we
run the firmware's own SPI routines to read flash and a private, sector-bound
bridge to erase/program exactly one reviewed sector. Every transaction is tracked;
an uncertain completion is never retried (KEEP THE CAMERA POWERED and inspect).
"""
import hashlib
import json
import os
import struct
from pathlib import Path

import usb.core
import usb.util

from or1k_subset import Assembler

BIAS = 0x02000000-0x2400                 # application RAM = flash offset + BIAS
ORIGINAL_SHA = 'e22557a4497a1199c9ecc3956b18a89ac70af800b674055513f3de91bfb8f224'   # stock flash dump
VID, PID = 0x1908, 0x3283
CHUNK = 65536
FLASH_SIZE = 4*1024*1024
BEFORE, AFTER = b'\xa5'*64, b'\x5a'*64
# USB-screen LCD descriptors (52 bytes) as the stock firmware sets them up in
# USB mode; identical on stock and patched firmware (deterministic boot heap).
LCD_DESCRIPTORS = {
    0x0208a6e4: bytes.fromhex('0000000000220d0200620e0200220d0200620e020000000000e0010000000000f000400100000000000000000001000000000100'),
    0x0208a718: bytes.fromhex('0000000000020f020042100200020f02004210020000000000e0010000000000f000400100000000000000000001000100010100')}


def sha(data): return hashlib.sha256(data).hexdigest()
def overlaps(a, n, b, m): return a < b+m and b < a+n


def read_transaction(dev, tag, cdb, length):
    cbw = struct.pack('<4sIIBBB16s', b'USBC', tag, length, 0x80, 0, len(cdb), cdb)
    if dev.write(0x01, cbw, timeout=2000) != 31: raise RuntimeError('short command write; no retry')
    data = bytes(dev.read(0x81, length, timeout=2000))
    csw = bytes(dev.read(0x81, 13, timeout=2000))
    if len(csw) != 13 or struct.unpack('<4sIIB', csw) != (b'USBS', tag, 0, 0): raise RuntimeError(f'unexpected status {csw.hex()}')
    if len(data) != length: raise RuntimeError('short data transfer')
    return data


def read_mem(dev, tag, address, size):
    cdb = b'\xcd'+struct.pack('<III', 0x0204109C, address, 0xFFFFFFFF)+b'\0'*3
    return read_transaction(dev, tag, cdb, size)


def callback(dev, tag, fn, address, length=8, timeout=2000):
    cdb = b'\xcd'+struct.pack('<III', 0x0204109C, address, fn)+b'\0'*3
    cbw = struct.pack('<4sIIBBB16s', b'USBC', tag, length, 128, 0, 16, cdb)
    if dev.write(1, cbw, timeout=2000) != 31: raise RuntimeError('short command')
    data = bytes(dev.read(0x81, length, timeout=timeout))
    csw = bytes(dev.read(0x81, 13, timeout=2000))
    if len(data) != length or len(csw) != 13 or struct.unpack('<4sIIB', csw) != (b'USBS', tag, 0, 0):
        raise RuntimeError('invalid callback reply; no retry')
    return data


def bridge(target, args, code_address, result_address):
    """Tiny RAM stub: call target(args...), store r11 and a 0x1970 marker."""
    a = Assembler()
    a.emit(0xd7e14ffc); a.immediate(0x27, 1, 1, -8)
    for reg, value in enumerate(args, 3):
        a.immediate(6, reg, 0, value >> 16); a.immediate(0x2a, reg, reg, value)
    pc = code_address+4*len(a.words)
    a.emit(1 << 26 | (((target-pc)//4) & 0x3ffffff))
    a.immediate(6, 3, 0, result_address >> 16); a.immediate(0x2a, 3, 3, result_address)
    a.emit(0x35 << 26 | 3 << 16 | 11 << 11)
    a.immediate(0x27, 4, 0, 0x1970); a.emit(0x35 << 26 | 3 << 16 | 4 << 11 | 4)
    a.immediate(0x27, 1, 1, 8); a.immediate(0x21, 9, 1, -4); a.emit(0x44004800)
    code = a.finish()
    assert len(code) <= 512
    return code


def journal(out, event, **data):
    """Persist intent before a mutation. A missing completion is NOT permission to retry."""
    with (out/'journal.jsonl').open('a') as f:
        f.write(json.dumps({'event': event, **data})+'\n'); f.flush(); os.fsync(f.fileno())


class Session:
    """Explicitly tracked, bounded transactions; never resets/retries the device."""
    def __init__(self, original, out):
        self.original = original; self.out = out; self.tag = 1200
        self.dev = None; self.claimed = False; self.detached = False; self.certain = True
        self.allowed = []; self.backup = None; self.owned = []; self.enable = None

    def read(self, address, size):
        result = bytearray()
        for off in range(0, size, 4096):
            self.tag += 1; self.certain = False
            result.extend(read_mem(self.dev, self.tag, address+off, min(4096, size-off)))
            self.certain = True
        return bytes(result)

    def write(self, address, data):
        if not data or not any(lo <= address and address+len(data) <= hi for lo, hi in self.allowed):
            raise RuntimeError('unowned write range')
        for off in range(0, len(data), 4096):
            chunk = data[off:off+4096]; self.tag += 1; self.certain = False
            cdb = b'\xcd'+struct.pack('<III', 0x0204109c, address+off, BIAS+0x29790)+b'\0'*3
            cmd = struct.pack('<4sIIBBB16s', b'USBC', self.tag, len(chunk), 0, 0, 16, cdb)
            if self.dev.write(1, cmd, timeout=2000) != 31: raise RuntimeError('short command')
            if self.dev.write(1, chunk, timeout=2000) != len(chunk): raise RuntimeError('short data')
            status = bytes(self.dev.read(0x81, 13, timeout=2000))
            if status != struct.pack('<4sIIB', b'USBS', self.tag, 0, 0): raise RuntimeError('bad status')
            self.certain = True
        if self.read(address, len(data)) != data: raise RuntimeError('readback mismatch')

    def call(self, address, result=None, size=8, timeout=2000):
        self.tag += 1; self.certain = False
        data = callback(self.dev, self.tag, address, self.result if result is None else result, size, timeout)
        self.certain = True
        return data

    def invoke(self, address, args, timeout=30000):
        self.idle()
        self.write(self.result, b'\0'*8)
        self.write(self.code, bridge(address, args, self.code, self.result))
        self.call(BIAS+0x2974c)
        value, marker = struct.unpack('<II', self.call(self.code, timeout=timeout))
        if marker != 0x1970:
            self.certain = False; raise RuntimeError('native helper did not return; power cycle required')
        return value

    def idle(self):
        if self.read(0x02085e9c, 4) != struct.pack('<I', 9): raise RuntimeError('leave the camera on its USB screen')
        if self.read(0x02086988, 4) != struct.pack('<I', self.desc) or self.read(self.desc, 52) != self.expected:
            raise RuntimeError('borrowed USB descriptor changed')
        if self.read(0x02088414, 4) != b'\0'*4: raise RuntimeError('printer is busy')

    def valid_allocation(self, pointer, size):
        if pointer % 32 or not 0x02090000 <= pointer <= 0x02200000-size: raise RuntimeError('invalid heap allocation')
        for low, length in self.frame_ranges+self.owned:
            if overlaps(pointer, size, low, length): raise RuntimeError('allocation overlaps live memory')

    def allocate(self, size):
        pointer = self.invoke(BIAS+0x3ace0, [size, 64])
        if not pointer: raise RuntimeError('not enough free RAM')
        self.valid_allocation(pointer, size)
        self.owned.append((pointer, size)); self.allowed.append((pointer, pointer+size))
        return pointer

    def open(self):
        devices = list(usb.core.find(find_all=True, idVendor=VID, idProduct=PID))
        if len(devices) != 1: raise RuntimeError('expected exactly one camera in USB mode (1908:3283)')
        self.dev = devices[0]
        intf = usb.util.find_descriptor(self.dev.get_active_configuration(), bInterfaceNumber=4, bAlternateSetting=0)
        if intf is None or (intf.bInterfaceClass, intf.bInterfaceSubClass, intf.bInterfaceProtocol) != (8, 6, 80):
            raise RuntimeError('wrong interface')
        if sorted((e.bEndpointAddress, e.bmAttributes) for e in intf) != [(1, 2), (129, 2)]: raise RuntimeError('wrong endpoints')
        sysfs = Path('/sys/bus/usb/devices')
        if sysfs.exists():
            for node in sysfs.iterdir():
                if ':' in node.name or not (node/'busnum').exists(): continue
                if int((node/'busnum').read_text()) == self.dev.bus and int((node/'devnum').read_text()) == self.dev.address:
                    if any(int(p.read_text()) for p in node.resolve().glob('**/block/*/size')): raise RuntimeError('remove the memory card')
        if self.dev.is_kernel_driver_active(4): self.dev.detach_kernel_driver(4); self.detached = True
        usb.util.claim_interface(self.dev, 4); self.claimed = True
        for off, n in ((0x4341c, 304), (0x2974c, 316), (0x3ab4c, 0x264), (0x4ba2c, 0x4ac), (0x3e7a4, 0xa48), (0x368fc, 0x1a4)):
            if self.read(BIAS+off, n) != self.original[off:off+n]: raise RuntimeError(f'unexpected firmware helper at {off:#x}')
        self.desc = struct.unpack('<I', self.read(0x02086988, 4))[0]
        if self.desc not in LCD_DESCRIPTORS: raise RuntimeError('unknown USB LCD buffer')
        self.expected = bytearray(LCD_DESCRIPTORS[self.desc]); self.expected[49] = 1
        pixels = struct.unpack_from('<I', self.expected, 4)[0]
        self.code = pixels+0x1e000-512; self.result = self.code-64
        self.idle(); self.allowed = [(self.code, self.code+512), (self.result, self.result+8)]
        self.frame_ranges = []
        for desc in (0x0208a640, 0x0208a674, 0x0208a6e4, 0x0208a718):
            data = self.read(desc, 52); pointer = struct.unpack_from('<I', data, 4)[0]; size = struct.unpack_from('<I', data, 24)[0]
            if not size or not 0x02090000 <= pointer <= 0x02200000-size: raise RuntimeError('unexpected live image allocation')
            self.frame_ranges.append((pointer, size))
        self.enable = self.read(0x02086970, 1)[0]
        if self.enable not in (0, 1): raise RuntimeError('unexpected display enable state')
        self.backup = (self.read(self.result, 8), self.read(self.code, 512))
        (self.out/'scratch_before.bin').write_bytes(b''.join(self.backup))

    def close(self):
        try:
            if self.certain and self.backup is not None:
                self.idle()
                for pointer, size in reversed(self.owned): self.invoke(BIAS+0x3adb0, [pointer])
                self.owned = []
                if self.read(0x02086970, 1) != bytes([self.enable]): self.invoke(BIAS+0x368fc, [self.enable])
                self.write(self.result, self.backup[0]); self.write(self.code, self.backup[1])
            elif not self.certain:
                print('USB completion uncertain: no retries, frees or restoration. Keep the camera powered.', flush=True)
        finally:
            if self.claimed: usb.util.release_interface(self.dev, 4)
            if self.detached and self.certain:
                try: self.dev.attach_kernel_driver(4)
                except usb.core.USBError as exc: print('Driver restore:', exc)
            if self.dev is not None: usb.util.dispose_resources(self.dev)


def heap(read):
    """Walk the stock allocator records (validated)."""
    node = struct.unpack('<I', read(0x02087c04, 4))[0]; seen = set(); records = []
    while node:
        if node in seen or not 0x02087c0c <= node < 0x0208800c or (node-0x02087c0c) % 16: raise RuntimeError('invalid allocator chain')
        seen.add(node)
        used, pointer, size, nxt = struct.unpack('<4I', read(node, 16))
        if node == 0x02087c0c and (used, pointer, size, nxt) == (1, 0, 0, 0): break
        if used not in (0, 1) or not size or not 0x02090000 <= pointer <= 0x02200000-size: raise RuntimeError('invalid allocator record')
        records.append({'node': node, 'used': used, 'pointer': pointer, 'size': size}); node = nxt
    if not records: raise RuntimeError('empty heap')
    return records


def snapshot(s, base, pass_number, out, total_passes=2):
    """One full 4 MiB flash read through the stock SPI READ routine, guarded."""
    result = bytearray()
    for offset in range(0, FLASH_SIZE, CHUNK):
        poison = bytes([(offset//CHUNK+pass_number*127) & 255])*CHUNK
        s.write(base, BEFORE+poison+AFTER)
        s.invoke(BIAS+0x29790, [base, CHUNK+128])
        if s.invoke(BIAS+0x39528, [offset, base+64, CHUNK]): raise RuntimeError('stock SPI read returned nonzero')
        s.invoke(BIAS+0x2980c, [base, CHUNK+128])
        data = s.read(base, CHUNK+128)
        if data[:64] != BEFORE or data[-64:] != AFTER: s.certain = False; raise RuntimeError('SPI DMA guard corruption; stop')
        result.extend(data[64:-64])
        if offset == 0 and result[:0x2400] != s.original[:0x2400]: raise RuntimeError('flash header differs from the stock firmware')
        if (offset+CHUNK) % (512*1024) == 0:
            print(f'Flash read {pass_number+1}/{total_passes}: {(offset+CHUNK)//1024} / 4096 KiB', flush=True)
    (out/f'flash-read-{pass_number+1}.bin').write_bytes(result)
    return bytes(result)


def read_flash(s, base, address, length, poison=0x36):
    if address % 16 or length % 16 or not 16 <= length <= CHUNK or not 0 <= address <= FLASH_SIZE-length: raise ValueError('invalid bounded read')
    s.write(base, BEFORE+bytes([poison])*length+AFTER)
    s.invoke(BIAS+0x29790, [base, length+128])
    if s.invoke(BIAS+0x39528, [address, base+64, length]): s.certain = False; raise RuntimeError('SPI read failed')
    s.invoke(BIAS+0x2980c, [base, length+128])
    raw = s.read(base, length+128)
    if raw[:64] != BEFORE or raw[-64:] != AFTER: s.certain = False; raise RuntimeError('SPI read guards damaged')
    return raw[64:-64]


PROBE_BYTES = 4096


def rehearse(s, probe, payload, out, mismatch=False):
    """Run the real boot loader in owned RAM against scratch hook mirrors: once with a
    deliberately wrong last hook (must reject and free) and once for success
    (exact relocation), without touching any live code. From usb_loader_rehearsal."""
    from persistent_loader import build_loader
    from persistent_payload import unpack, relocate, relocate_hooks
    from persistent_penguins import SIZE
    blob, relocs, hooks = unpack(payload, expected_size=SIZE)
    mirror = probe+2048; record = probe+2304
    code = build_loader(payload, probe, size=SIZE, rehearsal=(mirror, record))
    if len(code) > 2048: raise RuntimeError('diagnostic overlaps mirror')
    raw = bytearray(b'\xa5'*PROBE_BYTES); raw[:len(code)] = code
    for i, (_, old, _, _) in enumerate(hooks): struct.pack_into('<I', raw, 2048+i*4, old)
    if mismatch: raw[2048+(len(hooks)-1)*4] ^= 1
    raw[2304:2308] = b'\0'*4
    case = 'last-hook-mismatch' if mismatch else 'success'
    s.write(probe, bytes(raw)); s.call(BIAS+0x2974c)
    used = lambda: {(r['pointer'], r['size']) for r in heap(s.read) if r['used']}
    prior = used()
    status = s.invoke(probe, [])
    after = s.read(probe, PROBE_BYTES)
    pointer = struct.unpack_from('<I', after, 2304)[0]
    current = used()
    length = ((((len(payload)+15) & ~15)+32)+63) & ~63
    s.valid_allocation(pointer, length)
    if mismatch:
        if status != 8 or current != prior: s.certain = False; raise RuntimeError('rejection did not free its allocation')
        struct.pack_into('<I', raw, 2304, pointer)
        if after != raw: s.certain = False; raise RuntimeError('rejected loader changed scratch')
    else:
        if status or current-prior != {(pointer, length)} or prior-current:
            s.certain = False; raise RuntimeError('loader status or allocation differs')
        s.owned.append((pointer, length))
        for i, (_, _, new) in enumerate(relocate_hooks(hooks, pointer+64, size=SIZE)): struct.pack_into('<I', raw, 2048+i*4, new)
        struct.pack_into('<I', raw, 2304, pointer)
        if after != raw: s.certain = False; raise RuntimeError('mirror/code guards differ')
        loaded = s.read(pointer+32, (len(payload)+15) & ~15)
        expected = payload[:32]+relocate(blob, relocs, pointer+64, size=SIZE)+payload[32+SIZE:]
        if loaded != expected.ljust((len(payload)+15) & ~15, b'\xff'): s.certain = False; raise RuntimeError('relocated payload differs')
        s.invoke(BIAS+0x3adb0, [pointer]); s.owned.remove((pointer, length))
        if used() != prior: s.certain = False; raise RuntimeError('probe allocation not released')
    journal(out, 'rehearsal_complete', case=case, status=status, allocation=pointer)
    print(f'  loader rehearsal ({case}): ok', flush=True)


# --- private sector-bound SPI bridge (from persistent_upgrade04.sector_bridge) ---
SECTOR = 4096
ERASE, PAGE, PROGRAM, BRIDGE_SIZE = 0, 0x100, 0x500, 0x900


class _Code(Assembler):
    def const(self, r, value):
        self.immediate(6, r, 0, value >> 16); self.immediate(0x2a, r, r, value)
    def store(self, r, base, off=0, op=0x35):
        self.emit(op << 26 | ((off & 65535) >> 11) << 21 | base << 16 | r << 11 | (off & 2047))
    def compare(self, a, b, condition=1): self.emit(0x39 << 26 | condition << 21 | a << 16 | b << 11)


def _branch(source, target):
    delta = target-source
    if delta % 4 or not -(1 << 27) <= delta < (1 << 27): raise ValueError('invalid branch')
    return (delta//4) & 0x3ffffff


def sector_bridge(original, origin, sector, source, allowed):
    """RAM code that can erase exactly `sector` and program its 16 pages only from
    `source`. It replays the two displaced stock instructions and joins the stock
    erase/page-program routines after their settings-only entry guards."""
    if sha(original) != ORIGINAL_SHA or sector not in allowed: raise ValueError('unreviewed code/sector')
    if (origin % 64 or source % 64 or not 0x02090000 <= origin <= 0x021ff000-BRIDGE_SIZE
            or not 0x02090000 <= source <= 0x021ff000-SECTOR
            or (origin < source+SECTOR and source < origin+BRIDGE_SIZE)):
        raise ValueError('invalid private RAM bounds')
    blob = bytearray(struct.pack('<I', 0x14000000)*(BRIDGE_SIZE//4))
    for offset, entry in ((ERASE, 0x3970c), (PAGE, 0x395f0)):
        a = _Code(); imm = a.immediate
        if offset == ERASE:
            a.const(11, sector); a.compare(3, 11); a.branch(4, 'deny')
        else:
            for page in range(16):
                a.const(11, sector+page*256); a.compare(3, 11); a.branch(4, f'next{page}')
                a.const(11, source+page*256); a.compare(4, 11); a.branch(4, 'deny'); a.branch(0, 'allow')
                a.label(f'next{page}')
            a.branch(0, 'deny')
        a.label('allow')
        for off in (entry, entry+4): a.emit(struct.unpack_from('<I', original, off)[0])
        a.emit(_branch(origin+offset+len(a.words)*4, BIAS+entry+8))
        a.label('deny'); imm(0x27, 11, 0, -1); a.emit(0x44004800)
        code = a.finish()
        if offset+len(code) > (PAGE if offset == ERASE else PROGRAM): raise ValueError('bridge overflow')
        blob[offset:offset+len(code)] = code
    a = _Code(); imm = a.immediate
    for reg, value in ((3, sector), (4, source), (5, SECTOR)):
        a.const(11, value); a.compare(reg, 11); a.branch(4, 'deny')
    imm(0x27, 1, 1, -4); a.store(9, 1)
    for page in range(16):
        a.const(3, sector+page*256); a.const(4, source+page*256)
        a.emit(1 << 26 | _branch(origin+PROGRAM+len(a.words)*4, origin+PAGE))
        imm(0x2f, 1, 11, 0); a.branch(4, 'done')
    a.label('done'); imm(0x21, 9, 1, 0); imm(0x27, 1, 1, 4); a.emit(0x44004800)
    a.label('deny'); imm(0x27, 11, 0, -1); a.emit(0x44004800)
    code = a.finish()
    if PROGRAM+len(code) > BRIDGE_SIZE: raise ValueError('program overflow')
    blob[PROGRAM:PROGRAM+len(code)] = code
    return bytes(blob)
