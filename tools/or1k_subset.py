"""Small, strict no-delay OpenRISC interpreter for isolated pixel routines.

Not a camera emulator: no peripherals, interrupts, caches, or boot validation.
Instructions not implemented here fail explicitly.
"""
import struct

MASK = 0xffffffff


def signed(value, bits=32):
    value &= (1 << bits) - 1
    return value - (1 << bits) if value >> (bits - 1) else value


def branch(source, target):
    """Little-endian l.j word jumping from `source` to `target` (both byte addresses)."""
    delta = target-source
    if delta % 4 or not -(1 << 27) <= delta < (1 << 27): raise ValueError('invalid branch')
    return struct.pack('<I', (delta//4) & 0x3ffffff)


class CPU:
    def __init__(self, regions):
        self.regions = regions  # (base, bytearray, writable)
        self.r = [0] * 32
        self.flag = False
        self.writes = []
        self.steps = 0

    def access(self, address, size, value=None):
        for base, data, writable in self.regions:
            offset = address - base
            if 0 <= offset and offset + size <= len(data):
                if value is None:
                    return int.from_bytes(data[offset:offset+size], 'little')
                if not writable:
                    raise ValueError(f'write to read-only region at {address:#x}')
                data[offset:offset+size] = (value & ((1 << (size*8))-1)).to_bytes(size, 'little')
                self.writes.append((address, size))
                return
        raise ValueError(f'unmapped access at {address:#x}, size {size}')

    def run(self, entry, stop=0xfffffffc, limit=10000000):
        pc = entry
        self.r[9] = stop
        while pc != stop:
            self.steps += 1
            if self.steps > limit:
                raise ValueError('instruction budget exhausted')
            w = self.access(pc, 4)
            op, d, a, b = w >> 26, (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31
            imm = w & 0xffff
            r = self.r
            nxt = (pc + 4) & MASK
            if op in (0, 3, 4):
                if op == 0 or (op == 3 and not self.flag) or (op == 4 and self.flag):
                    nxt = (pc + signed(w & 0x3ffffff, 26)*4) & MASK
            elif op == 1:  # no-delay jal (AX3295B); tested return is pc+4
                r[9] = nxt
                nxt = (pc + signed(w & 0x3ffffff, 26)*4) & MASK
            elif op == 5:  # nop
                pass
            elif op == 6: r[d] = imm << 16
            elif op == 17: nxt = r[b]  # jr
            elif op == 18: nxt, r[9] = r[b], nxt  # jalr (no delay slot)
            elif op in (0x21, 0x23, 0x24, 0x25):
                size = {0x21:4, 0x23:1, 0x24:1, 0x25:2}[op]
                r[d] = self.access((r[a] + signed(imm, 16)) & MASK, size)
                if op == 0x24: r[d] = signed(r[d], 8)
            elif op == 0x27: r[d] = r[a] + signed(imm, 16)
            elif op == 0x29: r[d] = r[a] & imm
            elif op == 0x2a: r[d] = r[a] | imm
            elif op == 0x2b: r[d] = r[a] ^ signed(imm, 16)
            elif op == 0x2e:
                mode, shift = (w >> 6) & 3, w & 31
                if mode == 0: r[d] = r[a] << shift
                elif mode == 1: r[d] = r[a] >> shift
                elif mode == 2: r[d] = signed(r[a]) >> shift
                else: raise ValueError('unsupported rotate')
            elif op in (0x2f, 0x39):
                left = r[a]
                right = (signed(imm, 16) & MASK) if op == 0x2f else r[b]
                cond = d
                if cond >= 10: left, right = signed(left), signed(right)
                operations = {0:lambda: left == right, 1:lambda: left != right,
                              2:lambda: left > right, 3:lambda: left >= right,
                              4:lambda: left < right, 5:lambda: left <= right,
                              10:lambda: left > right, 11:lambda: left >= right,
                              12:lambda: left < right, 13:lambda: left <= right}
                self.flag = operations[cond]()
            elif op in (0x35, 0x36, 0x37):
                offset = signed(((w >> 21) & 31) << 11 | (w & 2047), 16)
                self.access((r[a] + offset) & MASK, {0x35:4, 0x36:1, 0x37:2}[op], r[b])
            elif op == 0x38:
                f = w & 0x7ff
                if f == 0: r[d] = r[a] + r[b]
                elif f == 2: r[d] = r[a] - r[b]
                elif f == 3: r[d] = r[a] & r[b]
                elif f == 4: r[d] = r[a] | r[b]
                elif f == 5: r[d] = r[a] ^ r[b]
                elif f == 8: r[d] = r[a] << (r[b] & 31)
                elif f == 0x48: r[d] = r[a] >> (r[b] & 31)
                elif f == 0x306: r[d] = r[a] * r[b]
                elif f == 0x309:
                    x, y = signed(r[a]), signed(r[b])
                    r[d] = (abs(x)//abs(y)) * (-1 if (x < 0) != (y < 0) else 1)
                elif f == 0x30a: r[d] = r[a] // r[b]
                else: raise ValueError(f'unsupported ALU {f:#x} at {pc:#x}')
            else:
                raise ValueError(f'unsupported instruction {w:08x} at {pc:#x}')
            self.r = [v & MASK for v in r]
            self.r[0] = 0
            pc = nxt
        return self


class Assembler:
    """Encode only the instructions used by our replacement pixel loop."""
    def __init__(self):
        self.words, self.labels, self.fixups = [], {}, []

    def emit(self, word): self.words.append(word)
    def label(self, name): self.labels[name] = len(self.words)*4
    def immediate(self, op, d, a, imm): self.emit(op << 26 | d << 21 | a << 16 | (imm & 0xffff))
    def alu(self, d, a, b, f=0): self.emit(0x38 << 26 | d << 21 | a << 16 | b << 11 | f)
    def branch(self, op, label):
        self.fixups.append((len(self.words), op, label))
        self.emit(0)
    def finish(self):
        for index, op, name in self.fixups:
            delta = self.labels[name]//4 - index
            if not -(1 << 25) <= delta < (1 << 25): raise ValueError('branch out of range')
            self.words[index] = op << 26 | (delta & 0x3ffffff)
        return struct.pack('<' + 'I'*len(self.words), *self.words)
