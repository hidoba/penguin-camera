"""Native no-delay AX329x code generation, with explicit MK1 memory bounds."""
import struct
from penguin_port.or1k_subset import Assembler, CPU

BIAS = 0x01ffda00
HEAP = 0x020e0000
RAM_END = 0x027fec00
MAX_DIM = 1536

def branch(source, target, link=False):
    delta = target-source
    if delta % 4 or not -(1 << 27) <= delta < (1 << 27):
        raise ValueError('invalid native branch')
    return struct.pack('<I', (int(link) << 26) | ((delta//4) & 0x3ffffff))

class Code(Assembler):
    def const(self, r, value):
        self.immediate(6, r, 0, value >> 16)
        self.immediate(0x2a, r, r, value)
    def store(self, r, base, off=0, op=0x35):
        self.emit(op << 26 | ((off & 65535) >> 11) << 21 | base << 16 | r << 11 | (off & 2047))
    def compare(self, a, b, condition=1):
        self.emit(0x39 << 26 | condition << 21 | a << 16 | b << 11)
    def call(self, origin, target):
        self.emit(int.from_bytes(branch(origin+len(self.words)*4, target, True), 'little'))
    def tail(self, origin, target):
        self.emit(int.from_bytes(branch(origin+len(self.words)*4, target), 'little'))

SAVED = tuple(r for r in range(2, 32) if r not in (9, 11))

def kernel_prologue(a, base, max_dimension, allocation_size, spill=0):
    """Shared bounded halftone ABI. Scratch ownership is supplied by its adapter."""
    imm = a.immediate
    a.spill = spill
    frame = spill+len(SAVED)*4
    imm(0x27, 1, 1, -frame)
    for i, r in enumerate(SAVED): a.store(r, 1, spill+i*4)
    for r, minimum in ((4, 8), (5, 1)):
        imm(0x2f, 4, r, minimum); a.branch(4, 'reject')
        imm(0x2f, 2, r, max_dimension); a.branch(4, 'reject')
    imm(0x29, 12, 6, 3); imm(0x2f, 1, 12, 0); a.branch(4, 'reject')
    a.alu(13, 4, 5, 0x306); a.alu(13, 3, 13)
    imm(0x27, 14, 0, 12); a.alu(14, 4, 14, 0x306); a.alu(15, 6, 14)
    for pointer, end in ((3, 13), (6, 15)):
        a.const(12, HEAP); a.compare(pointer, 12, 4); a.branch(4, 'reject')
        a.const(12, RAM_END); a.compare(end, 12, 2); a.branch(4, 'reject')
        a.compare(end, pointer, 5); a.branch(4, 'reject')
        a.compare(end, 1, 2); a.branch(4, 'reject')
    a.compare(3, 15, 3); a.branch(4, 'disjoint')
    a.compare(6, 13, 4); a.branch(4, 'reject')
    a.label('disjoint')
    return frame

def save(a, regs):
    a.immediate(0x27, 1, 1, -len(regs)*4)
    for i, r in enumerate(regs): a.store(r, 1, i*4)

def restore(a, regs):
    for i, r in enumerate(regs): a.immediate(0x21, r, 1, i*4)
    a.immediate(0x27, 1, 1, len(regs)*4)

def load_args(a, regs, wanted):
    for r in wanted: a.immediate(0x21, r, 1, regs.index(r)*4)
