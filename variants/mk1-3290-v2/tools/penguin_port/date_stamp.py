"""Vendored penguin camera math/native generator. Adaptations: MK1 RAM bounds; explicit UV input for orange; larger sensor width. No hardware access."""
import math, struct, json
from pathlib import Path
from mk1_native import Code
from penguin_port.preview_labels import GLYPHS

CLOCK = 0x02086a0c

SETTING = 0x02081ac0+17*4

SETTING_ON = 0x81000015

SCALE = 2

STROKE = 2

ADVANCE = 6*SCALE

X0 = 10

MARGIN = 10

CHARS = 10

TEXT_W = CHARS*ADVANCE-SCALE

TEXT_H = 7*SCALE

MIN_W = X0+TEXT_W+STROKE

MIN_H = MARGIN+TEXT_H+STROKE

FONT_CHARS = '0123456789-'

STAMP = 0xa200

STAMP_FONT = 0xa800

MAX_CODE = 0x600

def font():
    return b''.join(bytes(GLYPHS[c]) for c in FONT_CHARS)

def text(year, month, day):
    if not (2000 <= year <= 2099 and 1 <= month <= 12 and 1 <= day <= 31): return None
    return f'{year:04d}-{month:02d}-{day:02d}'

def reference(buf, w, h, year, month, day, enabled=True):
    out = bytearray(buf)
    t = text(year, month, day)
    if not enabled or t is None or w < MIN_W or h < MIN_H: return bytes(out)
    y0 = h-MARGIN-TEXT_H
    cells = [(X0+i*ADVANCE+c*SCALE, y0+r*SCALE) for i, ch in enumerate(t)
             for c, bits in enumerate(GLYPHS[ch]) for r in range(7) if bits >> r & 1]
    for pad, value in ((STROKE, 255), (0, 0)):
        for x, y in cells:
            for yy in range(y-pad, y+SCALE+pad):
                out[yy*w+x-pad:yy*w+x+SCALE+pad] = bytes([value])*(SCALE+2*pad)
    return bytes(out)

def stamp(base, clock=CLOCK, setting=None):
    """r3=buffer, r4=width, r5=height. Preserves every register (r11 too)."""
    a = Code(); imm = a.immediate
    saved = tuple(r for r in range(2, 32) if r != 9)
    frame = len(saved)*4+16                        # +16: ten digit codes
    imm(0x27, 1, 1, -frame)
    for i, r in enumerate(saved): a.store(r, 1, 16+i*4)
    imm(0x2f, 4, 4, MIN_W); a.branch(4, 'done')
    imm(0x2f, 2, 4, 1024); a.branch(4, 'done')
    imm(0x2f, 4, 5, MIN_H); a.branch(4, 'done')
    imm(0x2f, 2, 5, 1024); a.branch(4, 'done')
    a.const(12, 0x020e0000); a.compare(3, 12, 4); a.branch(4, 'done')
    a.alu(13, 4, 5, 0x306); a.alu(13, 3, 13)
    a.compare(13, 1, 2); a.branch(4, 'done')        # below our stack
    if setting is not None:
        # Obey the stock "print date" setting exactly as the stock UI tests it.
        a.const(12, setting); imm(0x21, 12, 12, 0); a.const(13, SETTING_ON)
        a.compare(12, 13, 1); a.branch(4, 'done')
    # date, validated
    a.const(12, clock); imm(0x25, 14, 12, 0); imm(0x23, 15, 12, 2); imm(0x23, 16, 12, 3)
    imm(0x2f, 4, 14, 2000); a.branch(4, 'done'); imm(0x2f, 2, 14, 2099); a.branch(4, 'done')
    imm(0x2f, 4, 15, 1); a.branch(4, 'done'); imm(0x2f, 2, 15, 12); a.branch(4, 'done')
    imm(0x2f, 4, 16, 1); a.branch(4, 'done'); imm(0x2f, 2, 16, 31); a.branch(4, 'done')
    # digit codes at sp+0..9
    imm(0x27, 17, 0, 10)
    def digits(value_reg, divisors, pos):
        for k, d in enumerate(divisors):
            imm(0x27, 18, 0, d); a.alu(19, value_reg, 18, 0x30a)          # v/d
            a.alu(19, 19, 17, 0x30a); a.alu(19, 19, 17, 0x306)           # (v/d/10)*10
            a.alu(20, value_reg, 18, 0x30a); a.alu(19, 20, 19, 2)        # (v/d)%10
            a.store(19, 1, pos+k, op=0x36)
    digits(14, (1000, 100, 10, 1), 0)
    a.store(17, 1, 4, op=0x36); digits(15, (10, 1), 5)
    a.store(17, 1, 7, op=0x36); digits(16, (10, 1), 8)
    a.const(20, base+STAMP_FONT)
    imm(0x27, 21, 5, -(MARGIN+TEXT_H)); a.alu(21, 21, 4, 0x306); a.alu(21, 3, 21)   # row y0
    imm(0x27, 21, 21, X0)                                                          # &buf[y0][X0]
    for pas, (pad, value) in enumerate(((STROKE, 255), (0, 0))):
        imm(0x27, 22, 0, value); imm(0x27, 23, 0, 0)                   # r23 = char index
        a.label(f'char{pas}')
        a.alu(24, 1, 23); imm(0x23, 24, 24, 0)                        # code
        imm(0x27, 25, 0, 5); a.alu(24, 24, 25, 0x306); a.alu(24, 20, 24)   # glyph ptr
        imm(0x27, 25, 0, ADVANCE); a.alu(25, 23, 25, 0x306); a.alu(25, 21, 25)  # char origin
        imm(0x27, 26, 0, 0)                                            # column
        a.label(f'col{pas}')
        a.alu(27, 24, 26); imm(0x23, 27, 27, 0)                       # bits
        imm(0x27, 28, 0, SCALE); a.alu(28, 26, 28, 0x306); a.alu(28, 25, 28)   # cell top-left
        a.label(f'bit{pas}')
        imm(0x29, 29, 27, 1); imm(0x2f, 0, 29, 0); a.branch(4, f'next_bit{pas}')
        # fill rect from (cell - pad*(w+1)) size (SCALE+2pad)^2
        imm(0x27, 29, 0, pad); a.alu(29, 29, 4, 0x306); imm(0x27, 29, 29, pad)
        a.alu(29, 28, 29, 2)
        imm(0x27, 30, 0, SCALE+2*pad)
        a.label(f'rect_row{pas}')
        imm(0x27, 31, 0, 0)
        a.label(f'rect_px{pas}')
        a.alu(18, 29, 31); a.store(22, 18, 0, op=0x36)
        imm(0x27, 31, 31, 1); imm(0x2f, 1, 31, SCALE+2*pad); a.branch(4, f'rect_px{pas}')
        a.alu(29, 29, 4); imm(0x27, 30, 30, -1); imm(0x2f, 1, 30, 0); a.branch(4, f'rect_row{pas}')
        a.label(f'next_bit{pas}')
        imm(0x27, 29, 0, SCALE); a.alu(29, 29, 4, 0x306); a.alu(28, 28, 29)     # next row of cells
        imm(0x2e, 27, 27, 0x41); imm(0x2f, 1, 27, 0); a.branch(4, f'bit{pas}')
        imm(0x27, 26, 26, 1); imm(0x2f, 1, 26, 5); a.branch(4, f'col{pas}')
        imm(0x27, 23, 23, 1); imm(0x2f, 1, 23, CHARS); a.branch(4, f'char{pas}')
    a.label('done')
    for i, r in enumerate(saved): imm(0x21, r, 1, 16+i*4)
    imm(0x27, 1, 1, frame); a.emit(0x44004800)
    code = a.finish()
    if len(code) > MAX_CODE: raise ValueError('stamp overflow')
    return code
