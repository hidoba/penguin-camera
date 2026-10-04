"""Minimal OpenRISC (no-delay-slot AX329x) disassembler for flash dumps."""
import struct, sys

def sx(v, b): return v - (1 << b) if v >> (b - 1) else v

def dis(w, pc):
    op = w >> 26; d = (w >> 21) & 31; a = (w >> 16) & 31; b = (w >> 11) & 31; i = w & 0xffff
    names = {0: 'l.j', 1: 'l.jal', 3: 'l.bnf', 4: 'l.bf'}
    if op in names: return f'{names[op]} {pc + sx(w & 0x3ffffff, 26) * 4:#x}'
    if op == 5: return 'l.nop'
    if op == 6: return f'l.movhi r{d},{i:#x}'
    if op == 0x11: return f'l.jr r{b}'
    if op == 0x12: return f'l.jalr r{b}'
    ld = {0x21: 'l.lwz', 0x22: 'l.lws', 0x23: 'l.lbz', 0x24: 'l.lbs', 0x25: 'l.lhz', 0x26: 'l.lhs'}
    if op in ld: return f'{ld[op]} r{d},{sx(i,16)}(r{a})'
    ai = {0x27: 'l.addi', 0x28: 'l.addic', 0x29: 'l.andi', 0x2a: 'l.ori', 0x2b: 'l.xori', 0x2c: 'l.muli'}
    if op in ai: return f'{ai[op]} r{d},r{a},{(sx(i,16) if op in (0x27,0x28,0x2b,0x2c) else hex(i))}'
    if op == 0x2d: return f'l.mfspr r{d},r{a},{i:#x}'
    if op == 0x30: return f'l.mtspr r{a},r{b},{((d<<11)|(w&0x7ff)):#x}'
    if op == 0x2e:
        t = ['slli', 'srli', 'srai', 'rori'][(w >> 6) & 3]; return f'l.{t} r{d},r{a},{w & 63}'
    if op == 0x2f:
        c = ['eq','ne','gtu','geu','ltu','leu'] + ['?']*4 + ['gts','ges','lts','les']
        return f'l.sf{c[d] if d < len(c) else d}i r{a},{sx(i,16)}'
    st = {0x35: 'l.sw', 0x36: 'l.sb', 0x37: 'l.sh'}
    if op in st: return f'{st[op]} {sx((d << 11) | (w & 0x7ff),16)}(r{a}),r{b}'
    if op == 0x38:
        f = w & 0xf; g = (w >> 6) & 3
        n = {0: 'add', 1: 'addc', 2: 'sub', 3: 'and', 4: 'or', 5: 'xor', 6: 'mul', 9: 'div', 10: 'divu', 11: 'mulu', 14: 'cmov', 15: 'ff1'}.get(f, f'alu{f}')
        if f == 8: n = ['sll', 'srl', 'sra', 'ror'][g]
        if f == 12: n = ['exths', 'extbs', 'exthz', 'extbz'][g]
        return f'l.{n} r{d},r{a},r{b}'
    if op == 0x39:
        c = ['eq','ne','gtu','geu','ltu','leu'] + ['?']*4 + ['gts','ges','lts','les']
        return f'l.sf{c[d] if d < len(c) else d} r{a},r{b}'
    if op == 0x08: return f'l.sys/trap {w:#x}'
    if op == 0x09: return 'l.rfe'
    return f'.word {w:#010x}'

def listing(data, start, count, bias=0):
    out = []
    for k in range(count):
        o = start + 4 * k; w = struct.unpack_from('<I', data, o)[0]
        out.append(f'{o:06x} {o + bias:08x}  {w:08x}  {dis(w, o + bias)}')
    return out

if __name__ == '__main__':
    d = open(sys.argv[1], 'rb').read(); bias = int(sys.argv[4], 16) if len(sys.argv) > 4 else 0
    print('\n'.join(listing(d, int(sys.argv[2], 16), int(sys.argv[3]), bias)))
