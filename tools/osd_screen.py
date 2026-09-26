"""Camera-mode screen overlay (update 25): curve name, dither name, clock, hints.

Stock UI overlay layer: 240x320 portrait 8-bit palette buffers (see osd_label).
Screen pixel (X, Y) = byte (319-X)*240 + Y. 249 transparent, 250 bar, 251 white.
Every owned rectangle spans whole 16-byte cache lines per column (Y ranges are
16-aligned multiples of 16), so a flush never writes back stale neighbours.

  top bar     X 0..285,  Y 0..31    bar      curve icon (OK button) + curve name (x2)
  bottom bar  X 0..270,  Y 208..239 bar      dither icon (4th button) + dither name (x2)
  clock       X 0..127,  Y 192..207 transp.  YYYY-MM-DD HH:MM:SS, 5x7, dark outline
  icon 2      X 0..27,   Y 64..111  transp.  switch camera (2nd button)
  icon 3      X 0..27,   Y 144..175 transp.  menu (3rd button)
Update 26: button pictograms (osd_icons) replace the text hints.
Stock keeps the battery (X 287..318 top) and the zoom "1.0X" (X 277..310 bottom).
The stock bottom-bar clock is covered; ours is redrawn on every preview frame and
at every overlay flip (the stock clock tick).
"""
import struct
from live_preview_patch import Code
from ui_trace_patch import BIAS
from preview_labels import GLYPHS, FONT, LABELS

TRANSPARENT, BAR, WHITE = 249, 250, 251
CLOCK = 0x02086a0c                    # u16 year, u8 month, day, hour, minute, second
FLUSH = BIAS+0x29790
BUFFER_BYTES = 240*320
OSD_CODE = 0xc400                     # free tail after the experimental Cracked slice kernel
OSD_END = 0xd000                      # sliced preview worker
COLON = (0x00, 0x00, 0x14, 0x00, 0x00)
REGIONS = ((0, 285, 0, 32, BAR), (0, 319, 208, 32, BAR),         # update 32: whole bottom bar (no zoom label)
           (0, 127, 192, 16, TRANSPARENT),
           (0, 27, 64, 48, TRANSPARENT), (0, 27, 144, 32, TRANSPARENT),
           (288, 319, 128, 80, TRANSPARENT))                              # update 28: right edge
CURVE_NAME = (32, 9)
DITHER_NAME = (32, 217)
CLOCK_AT = (3, 197)
ICON_AT = {'curve': (2, 4), 'camera': (2, 77), 'menu': (2, 144), 'dither': (2, 212)}
ICON_DATA = 0x9c80                    # free tail of the halftone kernel slot (..0xa000)
ICON_END = 0xa000
NO_DITHER_NAME = 'NO DITHERING'   # update 27: dither off is named
NO_DITHER_SLOT = 17
# Update 28: up/down hints. Dithering on: a small up/down glyph after the dither
# name. Dithering off: right edge, level with the up/down buttons: up triangle,
# framed-picture icon, down triangle (up/down change the stock frame/kaleidoscope).
ICON_DATA2 = 0xa840                 # free after the date-stamp font (..0xab00; trampoline at 0xab00)
ICON2_END = 0xab00
UPDOWN_GAP, UPDOWN_Y = 6, 216
UPDOWN_MAX_X = 319-12              # update 32: glyph only if it fits the owned bottom bar
RIGHT_TRI_UP, RIGHT_FRAME, RIGHT_TRI_DOWN = (296, 132), (290, 154), (296, 196)               # LABELS+17*32 (FLAT CURVE uses the curve-0 slot 12)
MAX_CHARS = 20


def offset(x, y): return (319-x)*240+y


def glyphs():
    g = dict(GLYPHS); g[':'] = COLON; return g


def clock_text(rtc):
    y, mo, d, h, mi, s = rtc
    if not (2000 <= y <= 2099 and 1 <= mo <= 12 and 1 <= d <= 31 and h <= 23 and mi <= 59 and s <= 59): return None
    return f'{y:04d}-{mo:02d}-{d:02d} {h:02d}:{mi:02d}:{s:02d}'


def draw(out, text, x, y, scale, outline):
    g = glyphs()
    cells = [(x+i*6*scale+c*scale, y+r*scale) for i, ch in enumerate(text[:MAX_CHARS])
             for c, bits in enumerate(g.get(ch, g[' '])) for r in range(7) if bits >> r & 1]
    for pad, colour in (((1, BAR),) if outline else ())+((0, WHITE),):
        for cx, cy in cells:
            for xx in range(cx-pad, cx+scale+pad):
                for yy in range(cy-pad, cy+scale+pad): out[offset(xx, yy)] = colour


def blit(out, px, x, y, w, h):
    from osd_icons import TABLE
    for r in range(h):
        for c in range(w):
            v = px[r*w+c]
            if v != 15: out[offset(x+c, y+r)] = TABLE[v]


def reference(layer, enabled, mode, curve, rtc, names, curve_names, max_mode, curve_luts=None):
    """curve_names[state]; curve_luts: {state: 256-byte LUT} (missing -> identity)."""
    import osd_icons as ic
    out = bytearray(layer)
    for x0, x1, y0, rows, v in REGIONS:
        for x in range(x0, x1+1):
            for y in range(y0, y0+rows): out[offset(x, y)] = v
    if 0 <= curve < len(curve_names): draw(out, curve_names[curve], *CURVE_NAME, 2, False)
    if not enabled: draw(out, NO_DITHER_NAME, *DITHER_NAME, 2, False)
    elif 0 <= mode <= max_mode:
        end = DITHER_NAME[0]+len(names[mode][:MAX_CHARS])*12
        draw(out, names[mode], *DITHER_NAME, 2, False)
        if end+UPDOWN_GAP <= UPDOWN_MAX_X:
            blit(out, ic.ICONS2['updown'], end+UPDOWN_GAP, UPDOWN_Y, ic.UPDOWN_W, ic.UPDOWN_H)
    t = clock_text(rtc)
    if t: draw(out, t, *CLOCK_AT, 1, True)
    for name in ('curve', 'camera', 'menu', 'dither'):
        x, y = ICON_AT[name]
        blit(out, ic.ICONS['frame'], x, y, 24, 24)
        if name == 'curve':
            lut = (curve_luts or {}).get(curve)
            lut = lut if lut is not None else bytes(range(256))
            for col, lo, hi in ic.curve_points(lut):
                for yy in range(lo, hi+1): out[offset(x+4+col, y+4+yy)] = WHITE
        else:
            key = name if name != 'dither' else ('dither_on' if enabled else 'dither_off')
            blit(out, ic.ICONS[key], x+4, y+4, 16, 16)
    if not enabled:
        blit(out, ic.ICONS2['tri_up'], *RIGHT_TRI_UP, ic.TRI_W, ic.TRI_H)
        blit(out, ic.ICONS2['painting'], *RIGHT_FRAME, ic.PAINTING_W, ic.PAINTING_H)
        blit(out, ic.ICONS2['tri_down'], *RIGHT_TRI_DOWN, ic.TRI_W, ic.TRI_H)
    return bytes(out)


# ---------------------------------------------------------------- native code

def fill_code():
    """r3 buf, r4 x0, r5 x1 (incl.), r6 y0 (4-aligned), r7 rows (x4), r8 fill word.
    Leaf; preserves every register."""
    a = Code(); imm = a.immediate
    saved = (12, 13, 14, 15)
    imm(0x27, 1, 1, -16)
    for i, r in enumerate(saved): a.store(r, 1, i*4)
    imm(0x2a, 12, 4, 0)
    a.label('col')
    imm(0x27, 13, 0, 319); a.alu(13, 13, 12, 2); imm(0x27, 14, 0, 240); a.alu(13, 13, 14, 0x306)
    a.alu(13, 13, 6); a.alu(13, 3, 13); a.alu(15, 13, 7)
    a.label('word'); a.store(8, 13, 0); imm(0x27, 13, 13, 4); a.compare(13, 15, 4); a.branch(4, 'word')
    imm(0x27, 12, 12, 1); a.compare(12, 5, 5); a.branch(4, 'col')
    for i, r in enumerate(saved): imm(0x21, r, 1, i*4)
    imm(0x27, 1, 1, 16); a.emit(0x44004800)
    return a.finish()


def text_code(base):
    """r3 buf, r4 x, r5 y, r6 NUL-terminated string, r7 scale (1|2), r8 outline (0|1).
    Leaf; preserves every register except r11 = x after the last character."""
    a = Code(); imm = a.immediate
    saved = tuple(range(12, 30))
    imm(0x27, 1, 1, -len(saved)*4)
    for i, r in enumerate(saved): a.store(r, 1, i*4)
    a.const(12, base+FONT)
    # r13 = 1: outline pass (pad 1, BAR) first when r8 != 0; r13 = 0: white pass
    imm(0x27, 13, 0, 0); imm(0x2f, 0, 8, 0); a.branch(4, 'setup'); imm(0x27, 13, 0, 1)
    a.label('setup')
    imm(0x27, 14, 0, WHITE); imm(0x27, 15, 0, 0)                 # colour, pad
    imm(0x2f, 0, 13, 0); a.branch(4, 'pad_set'); imm(0x27, 14, 0, BAR); imm(0x27, 15, 0, 1)
    a.label('pad_set')
    imm(0x27, 16, 0, 0)                                           # char index
    a.label('char')
    a.alu(17, 6, 16); imm(0x23, 17, 17, 0); imm(0x2f, 0, 17, 0); a.branch(4, 'pass_done')
    imm(0x2f, 2, 17, 127); a.branch(4, 'next_char')
    imm(0x27, 18, 0, 5); a.alu(17, 17, 18, 0x306); a.alu(17, 12, 17)          # glyph
    imm(0x27, 18, 0, 6); a.alu(18, 18, 7, 0x306); a.alu(18, 18, 16, 0x306); a.alu(18, 4, 18)   # char x
    imm(0x27, 19, 0, 0)                                           # column
    a.label('column')
    a.alu(20, 17, 19); imm(0x23, 20, 20, 0)                       # bits
    a.alu(21, 19, 7, 0x306); a.alu(21, 18, 21)                    # cell x
    imm(0x2a, 22, 5, 0)                                           # cell y
    a.label('bit')
    imm(0x29, 23, 20, 1); imm(0x2f, 0, 23, 0); a.branch(4, 'next_bit')
    # rect (cell x - pad .. + scale + pad) x (cell y - pad ..)
    a.alu(23, 7, 15); a.alu(23, 23, 15)                           # side = scale + 2 pad
    a.alu(24, 21, 15, 2)                                          # x start
    imm(0x27, 25, 0, 0)
    a.label('rx')
    a.alu(26, 24, 25); imm(0x27, 27, 0, 319); a.alu(26, 27, 26, 2)
    imm(0x27, 27, 0, 240); a.alu(26, 26, 27, 0x306); a.alu(26, 3, 26)
    a.alu(27, 22, 15, 2); a.alu(26, 26, 27)                       # column byte at y - pad
    imm(0x27, 28, 0, 0)
    a.label('ry'); a.alu(29, 26, 28); a.store(14, 29, 0, op=0x36)
    imm(0x27, 28, 28, 1); a.compare(28, 23, 4); a.branch(4, 'ry')
    imm(0x27, 25, 25, 1); a.compare(25, 23, 4); a.branch(4, 'rx')
    a.label('next_bit')
    a.alu(22, 22, 7); imm(0x2e, 20, 20, 0x41); imm(0x2f, 1, 20, 0); a.branch(4, 'bit')
    imm(0x27, 19, 19, 1); imm(0x2f, 1, 19, 5); a.branch(4, 'column')
    a.label('next_char')
    imm(0x27, 16, 16, 1); imm(0x2f, 4, 16, MAX_CHARS); a.branch(4, 'char')
    a.label('pass_done')
    imm(0x2f, 0, 13, 0); a.branch(4, 'done')
    imm(0x27, 13, 0, 0); a.branch(0, 'setup')
    a.label('done')
    imm(0x27, 11, 0, 6); a.alu(11, 11, 7, 0x306); a.alu(11, 11, 16, 0x306); a.alu(11, 11, 4)
    for i, r in enumerate(saved): imm(0x21, r, 1, i*4)
    imm(0x27, 1, 1, len(saved)*4); a.emit(0x44004800)
    return a.finish()


def blit_code(base):
    """r3 buf, r4 x, r5 y, r6 w, r7 h, r8 4-bit map (row-major). Nibble -> base+ICON_DATA
    table; 15 = skip. Column-major walk with a running byte pointer (update 32: no
    per-pixel multiply). Leaf; preserves every register."""
    a = Code(); imm = a.immediate
    saved = tuple(range(12, 23))
    imm(0x27, 1, 1, -len(saved)*4)
    for i, r in enumerate(saved): a.store(r, 1, i*4)
    a.const(12, base+ICON_DATA)
    imm(0x27, 13, 0, 319); a.alu(13, 13, 4, 2); imm(0x27, 14, 0, 240); a.alu(13, 13, 14, 0x306)
    a.alu(13, 13, 5); a.alu(13, 3, 13)                              # r13 = &pixel(x, y)
    imm(0x27, 15, 0, 0)                                              # col
    a.label('col'); imm(0x2a, 16, 15, 0); imm(0x27, 17, 0, 0)        # pixel index = col, row 0
    a.label('row')
    imm(0x2e, 18, 16, 0x41); a.alu(18, 8, 18); imm(0x23, 18, 18, 0)
    imm(0x29, 19, 16, 1); imm(0x2f, 0, 19, 0); a.branch(4, 'low'); imm(0x2e, 18, 18, 0x44)
    a.label('low'); imm(0x29, 18, 18, 15); imm(0x2f, 0, 18, 15); a.branch(4, 'next')
    a.alu(18, 12, 18); imm(0x23, 18, 18, 0); a.alu(19, 13, 17); a.store(18, 19, 0, op=0x36)
    a.label('next')
    a.alu(16, 16, 6); imm(0x27, 17, 17, 1); a.compare(17, 7, 4); a.branch(4, 'row')
    imm(0x27, 13, 13, -240); imm(0x27, 15, 15, 1); a.compare(15, 6, 4); a.branch(4, 'col')
    for i, r in enumerate(saved): imm(0x21, r, 1, i*4)
    imm(0x27, 1, 1, len(saved)*4); a.emit(0x44004800)
    return a.finish()


def plot_code():
    """r3 buf, r4 x, r5 y (16x16 inner origin), r6 LUT (0 = identity). White curve,
    column i spans the previous and current y = 15 - (lut[i*17] >> 4). Leaf."""
    a = Code(); imm = a.immediate
    saved = tuple(range(12, 22))
    imm(0x27, 1, 1, -len(saved)*4)
    for i, r in enumerate(saved): a.store(r, 1, i*4)
    imm(0x27, 12, 0, 0); imm(0x27, 20, 0, WHITE)                      # column
    a.label('col')
    imm(0x27, 13, 0, 17); a.alu(13, 12, 13, 0x306)                    # v = i*17
    imm(0x2f, 0, 6, 0); a.branch(4, 'have_v'); a.alu(13, 6, 13); imm(0x23, 13, 13, 0)
    a.label('have_v'); imm(0x2e, 13, 13, 0x44); imm(0x27, 14, 0, 15); a.alu(13, 14, 13, 2)   # y
    imm(0x2f, 1, 12, 0); a.branch(4, 'span'); imm(0x2a, 19, 13, 0)    # first column: prev = y
    a.label('span')
    imm(0x2a, 15, 13, 0); imm(0x2a, 16, 19, 0)                        # lo, hi = y, prev
    a.compare(15, 16, 5); a.branch(4, 'ordered'); imm(0x2a, 15, 19, 0); imm(0x2a, 16, 13, 0)
    a.label('ordered')
    a.alu(17, 4, 12); imm(0x27, 18, 0, 319); a.alu(17, 18, 17, 2)
    imm(0x27, 18, 0, 240); a.alu(17, 17, 18, 0x306); a.alu(17, 3, 17); a.alu(17, 17, 5)
    a.label('dot'); a.alu(18, 17, 15); a.store(20, 18, 0, op=0x36)
    imm(0x27, 15, 15, 1); a.compare(15, 16, 5); a.branch(4, 'dot')
    imm(0x2a, 19, 13, 0)
    imm(0x27, 12, 12, 1); imm(0x2f, 1, 12, 16); a.branch(4, 'col')
    for i, r in enumerate(saved): imm(0x21, r, 1, i*4)
    imm(0x27, 1, 1, len(saved)*4); a.emit(0x44004800)
    return a.finish()


def paint_code(base, origin, fill, text, blit, plot, icons, curve_state, preview_state, max_mode,
               allocation_size, curve_label_mode0, curve_luts, icons2, curve_max):
    import osd_icons as ic
    icon_sizes = {'updown': (ic.UPDOWN_W, ic.UPDOWN_H), 'tri': (ic.TRI_W, ic.TRI_H)}
    """r3 = overlay buffer. Validates it, paints every region, flushes the buffer.
    Preserves every register except r11."""
    a = Code(); imm = a.immediate
    saved = tuple(r for r in range(2, 32) if r != 11)
    frame = len(saved)*4+32                                       # +32: clock text
    imm(0x27, 1, 1, -frame)
    for i, r in enumerate(saved): a.store(r, 1, 32+i*4)
    imm(0x29, 13, 3, 31); imm(0x2f, 1, 13, 0); a.branch(4, 'done')
    a.const(13, 0x02090000); a.compare(3, 13, 4); a.branch(4, 'done')
    a.const(13, 0x021ff000-BUFFER_BYTES); a.compare(3, 13, 2); a.branch(4, 'done')
    a.const(13, base+allocation_size); a.compare(3, 13, 3); a.branch(4, 'disjoint')
    a.const(13, BUFFER_BYTES); a.alu(13, 3, 13); a.const(14, base)
    a.compare(13, 14, 2); a.branch(4, 'done')
    a.label('disjoint')
    for x0, x1, y0, rows, v in REGIONS:
        imm(0x27, 4, 0, x0); imm(0x27, 5, 0, x1); imm(0x27, 6, 0, y0); imm(0x27, 7, 0, rows)
        a.const(8, v*0x01010101); a.call(origin, fill)
    def put(string_addr_setup, x, y, scale, outline):
        string_addr_setup()
        imm(0x27, 4, 0, x); imm(0x27, 5, 0, y); imm(0x27, 7, 0, scale); imm(0x27, 8, 0, outline)
        a.call(origin, text)
    # curve name
    a.const(12, base+curve_state); imm(0x21, 12, 12, 0)
    imm(0x2f, 2, 12, curve_max); a.branch(4, 'no_curve')
    def curve_addr():
        imm(0x27, 12, 12, curve_label_mode0+1); imm(0x2e, 12, 12, 5); a.const(6, base+LABELS); a.alu(6, 6, 12)
    put(curve_addr, *CURVE_NAME, 2, 0)
    a.label('no_curve')
    # dither name (+ up/down glyph after it) or NO DITHERING
    a.const(12, base+preview_state); imm(0x21, 13, 12, 0); imm(0x21, 12, 12, 4)
    def dither_addr():
        imm(0x27, 12, 12, 1); imm(0x2e, 12, 12, 5); a.const(6, base+LABELS); a.alu(6, 6, 12)
    imm(0x2f, 1, 13, 0); a.branch(4, 'dither_on'); imm(0x27, 12, 0, NO_DITHER_SLOT-1)
    put(dither_addr, *DITHER_NAME, 2, 0); a.branch(0, 'no_dither')
    a.label('dither_on'); imm(0x2f, 2, 12, max_mode); a.branch(4, 'no_dither')
    put(dither_addr, *DITHER_NAME, 2, 0)
    imm(0x27, 4, 11, UPDOWN_GAP); imm(0x2f, 2, 4, UPDOWN_MAX_X); a.branch(4, 'no_dither')
    imm(0x27, 5, 0, UPDOWN_Y); imm(0x27, 6, 0, icon_sizes['updown'][0])
    imm(0x27, 7, 0, icon_sizes['updown'][1]); a.const(8, base+ICON_DATA2+icons2['updown']); a.call(origin, blit)
    a.label('no_dither')
    # clock "YYYY-MM-DD HH:MM:SS" at sp+0
    a.const(12, CLOCK); imm(0x25, 14, 12, 0)
    imm(0x2f, 4, 14, 2000); a.branch(4, 'no_clock'); imm(0x2f, 2, 14, 2099); a.branch(4, 'no_clock')
    fields = []
    for off, lo, hi in ((2, 1, 12), (3, 1, 31), (4, 0, 23), (5, 0, 59), (6, 0, 59)):
        r = 15+len(fields); fields.append(r)
        imm(0x23, r, 12, off)
        if lo: imm(0x2f, 4, r, lo); a.branch(4, 'no_clock')
        imm(0x2f, 2, r, hi); a.branch(4, 'no_clock')
    imm(0x27, 20, 0, 10)
    def digits(value_reg, divisors, pos):
        for k, d in enumerate(divisors):
            imm(0x27, 21, 0, d); a.alu(22, value_reg, 21, 0x30a)          # v/d
            a.alu(23, 22, 20, 0x30a); a.alu(23, 23, 20, 0x306)           # (v/d/10)*10
            a.alu(22, 22, 23, 2); imm(0x27, 22, 22, ord('0'))            # (v/d)%10 + '0'
            a.store(22, 1, pos+k, op=0x36)
    digits(14, (1000, 100, 10, 1), 0)
    for (reg, pos, sep) in zip(fields, (5, 8, 11, 14, 17), '-- ::'):
        imm(0x27, 22, 0, ord(sep)); a.store(22, 1, pos-1, op=0x36)
        digits(reg, (10, 1), pos)
    a.store(0, 1, 19, op=0x36)
    put(lambda: imm(0x2a, 6, 1, 0), *CLOCK_AT, 1, 1)
    a.label('no_clock')
    # button pictograms (update 26)
    def icon(data_off, x, y, w, h):
        imm(0x27, 4, 0, x); imm(0x27, 5, 0, y); imm(0x27, 6, 0, w); imm(0x27, 7, 0, h)
        a.const(8, base+ICON_DATA+data_off); a.call(origin, blit)
    for name in ('curve', 'camera', 'menu', 'dither'):
        x, y = ICON_AT[name]
        icon(icons['frame'], x, y, 24, 24)
        if name in ('camera', 'menu'): icon(icons[name], x+4, y+4, 16, 16)
    # dither pictogram: dithered ramp when dithering is on, smooth grays when off
    a.const(12, base+preview_state); imm(0x21, 12, 12, 0)
    imm(0x2f, 0, 12, 0); a.branch(4, 'smooth')
    icon(icons['dither_on'], ICON_AT['dither'][0]+4, ICON_AT['dither'][1]+4, 16, 16); a.branch(0, 'dither_icon_done')
    a.label('smooth'); icon(icons['dither_off'], ICON_AT['dither'][0]+4, ICON_AT['dither'][1]+4, 16, 16)
    a.label('dither_icon_done')
    # curve pictogram: the selected curve's LUT (auto levels: the live preview LUT), else identity
    a.const(12, base+curve_state); imm(0x21, 12, 12, 0); imm(0x27, 6, 0, 0)
    for k, lut_off in sorted(curve_luts.items()):
        imm(0x2f, 1, 12, k); a.branch(4, f'not_curve{k}'); a.const(6, base+lut_off); a.branch(0, 'plot')
        a.label(f'not_curve{k}')
    a.label('plot')
    imm(0x27, 4, 0, ICON_AT['curve'][0]+4); imm(0x27, 5, 0, ICON_AT['curve'][1]+4); a.call(origin, plot)
    # right edge (dithering off): up/down change the frame
    a.const(12, base+preview_state); imm(0x21, 12, 12, 0); imm(0x2f, 1, 12, 0); a.branch(4, 'no_right')
    def icon2(name, xy, wh):
        imm(0x27, 4, 0, xy[0]); imm(0x27, 5, 0, xy[1]); imm(0x27, 6, 0, wh[0]); imm(0x27, 7, 0, wh[1])
        a.const(8, base+ICON_DATA2+icons2[name]); a.call(origin, blit)
    icon2('tri_up', RIGHT_TRI_UP, icon_sizes['tri'])
    icon2('painting', RIGHT_FRAME, (ic.PAINTING_W, ic.PAINTING_H))
    icon2('tri_down', RIGHT_TRI_DOWN, icon_sizes['tri'])
    a.label('no_right')
    imm(0x21, 3, 1, 32+saved.index(3)*4); a.const(4, BUFFER_BYTES); a.call(origin, FLUSH)
    a.label('done')
    for i, r in enumerate(saved): imm(0x21, r, 1, 32+i*4)
    imm(0x27, 1, 1, frame); a.emit(0x44004800)
    return a.finish()


def build(base, curve_state, preview_state, max_mode, allocation_size, curve_label_mode0, curve_luts, curve_max=4):
    """curve_luts: {curve value: payload offset of its 256-byte LUT}.
    Returns ({offset: bytes}, paint_offset); includes the icon data at ICON_DATA."""
    import osd_icons
    icon_blob, icons = osd_icons.blob()
    if ICON_DATA+len(icon_blob) > ICON_END: raise ValueError('icon data overflow')
    icon_blob2, icons2 = osd_icons.blob2()
    if ICON_DATA2+len(icon_blob2) > ICON2_END: raise ValueError('icon data 2 overflow')
    parts = {ICON_DATA: icon_blob, ICON_DATA2: icon_blob2}
    off = OSD_CODE; offs = {}
    for name, code in (('fill', fill_code()), ('text', text_code(base)), ('blit', blit_code(base)), ('plot', plot_code())):
        parts[off] = code; offs[name] = off; off = (off+len(code)+15) & ~15
    paint = paint_code(base, base+off, base+offs['fill'], base+offs['text'], base+offs['blit'], base+offs['plot'],
                       icons, curve_state, preview_state, max_mode, allocation_size, curve_label_mode0, curve_luts,
                       icons2, curve_max)
    if off+len(paint) > OSD_END: raise ValueError('osd screen overflow')
    parts[off] = paint
    return parts, off


def font_patch():
    """Glyph for ':' (0x3a) in the 5-byte-per-ASCII label font."""
    return {FONT+ord(':')*5: bytes(COLON)}


def screen_entry(base, paint):
    """Preview-callback entry at LABEL_CODE (same ABI as the old overlay: r3..r5
    ignored). Update 32: camera mode only, and only the buffer currently on screen
    (the spare one is painted by flip_wrapper right before it is shown).
    Preserves every register, r11 = 0."""
    from osd_label import _descriptor_ok, DESCRIPTOR, MODE_WORD
    from preview_labels import LABEL_CODE
    a = Code(); imm = a.immediate; origin = base+LABEL_CODE
    saved = (3, 9, 12, 13, 14)
    imm(0x27, 1, 1, -len(saved)*4)
    for i, r in enumerate(saved): a.store(r, 1, i*4)
    a.const(12, MODE_WORD); imm(0x21, 12, 12, 0); imm(0x2f, 1, 12, 3); a.branch(4, 'done')
    _descriptor_ok(a, 'done')
    imm(0x21, 13, 12, 0x20); imm(0x21, 14, 12, 0x10); a.compare(13, 14, 0); a.branch(4, 'in_use_ok')
    imm(0x21, 14, 12, 0x18); a.compare(13, 14, 1); a.branch(4, 'done')
    a.label('in_use_ok')
    imm(0x2a, 3, 13, 0); a.call(origin, paint)
    a.label('done')
    for i, r in enumerate(saved): imm(0x21, r, 1, i*4)
    imm(0x27, 1, 1, len(saved)*4); imm(0x27, 11, 0, 0); a.emit(0x44004800)
    code = a.finish()
    from osd_label import FLIP_WRAP
    if len(code) > FLIP_WRAP-LABEL_CODE: raise ValueError('screen entry overflow')
    return code


def flip_wrapper(base, paint):
    """Stands in for `jal 0x3783c` at 0x61d0 (r3 layer, r4 buffer). Camera mode,
    layer 0, one of the two descriptor buffers: wait for the stock DMA canvas copy,
    paint, then l.j into the stock flip with r3/r4/r9 intact."""
    from osd_label import _descriptor_ok, FLIP_WRAP, FLIP_TARGET, MODE_WORD, DMA_WAIT
    from or1k_subset import branch
    a = Code(); imm = a.immediate; origin = base+FLIP_WRAP
    saved = (3, 4, 5, 9, 12, 13, 14)
    imm(0x27, 1, 1, -len(saved)*4)
    for i, r in enumerate(saved): a.store(r, 1, i*4)
    a.const(12, MODE_WORD); imm(0x21, 12, 12, 0); imm(0x2f, 1, 12, 3); a.branch(4, 'tail')
    imm(0x2f, 1, 3, 0); a.branch(4, 'tail')
    _descriptor_ok(a, 'tail')
    imm(0x21, 13, 12, 0x10); a.compare(4, 13, 0); a.branch(4, 'buffer_ok')
    imm(0x21, 13, 12, 0x18); a.compare(4, 13, 1); a.branch(4, 'tail')
    a.label('buffer_ok')
    a.call(origin, DMA_WAIT)                                      # clobbers r3..r5
    imm(0x21, 3, 1, saved.index(4)*4); a.call(origin, paint)
    a.label('tail')
    for i, r in enumerate(saved): imm(0x21, r, 1, i*4)
    imm(0x27, 1, 1, len(saved)*4)
    code = a.finish()
    code += branch(origin+len(code), BIAS+FLIP_TARGET)
    if len(code) > 0x300: raise ValueError('flip wrapper overflow')
    return code


# Update 34: the stock camera-screen clock (widget 12, shown by the timer routine
# 0xa920 only while the stock "print date/time" setting is on) is drawn by the stock
# UI underneath our bar and could flash through it. Always take its hide path.
STOCK_CLOCK_BRANCH = 0xa958            # l.bf 0xa9b4 (setting off -> hide widget 12)


def stock_clock_hook(original):
    old = struct.unpack_from('<I', original, STOCK_CLOCK_BRANCH)[0]
    if old != 0x10000017: raise ValueError('unexpected stock clock branch')
    return {'address': BIAS+STOCK_CLOCK_BRANCH, 'original': struct.pack('<I', old).hex(),
            'replacement': struct.pack('<I', 0x00000017).hex(),       # l.j 0xa9b4
            'label': 'stock camera-screen clock widget always hidden (our clock replaces it)'}
