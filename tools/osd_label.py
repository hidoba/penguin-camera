"""Dither-mode name in the stock top icon bar (update 12).

The stock UI overlay is a 240x320 portrait 8-bit palette layer (descriptor at
0x020869a0: +8 = u16 240, u16 320; +0x10/+0x18 = two 76800-byte buffers; +0x20 =
buffer in use). Landscape screen pixel (X, Y) is layer byte (319-X)*240 + Y.
Index 249 = transparent, 250 = bar background, 251 = white UI text ("12M").
The top bar is screen rows 0..31; the camera pictogram ends at X=31 and "12M"
starts at X~227. This routine owns only X 34..224, Y 0..31 of that bar: every
preview frame it restores bar colour there and, in a dither mode, draws the
5x7 name at 2x in white. Nothing is drawn into the photo any more.
"""
from live_preview_patch import Code
from ui_trace_patch import BIAS
from preview_labels import GLYPHS, LABEL_CODE, FONT, LABELS

DESCRIPTOR = 0x020869a0
DIMS = 0x014000f0             # u16 240, u16 320 at +8
BUFFER_BYTES = 240*320
BAR, WHITE = 250, 251
# Update 24: own X 0..285, i.e. cover the camera pictogram (4..31), "12M" (227..251)
# and the printer icon (259..282); only the battery (287..318) stays stock.
X_MIN, X_MAX = 0, 285         # inclusive owned span in screen X
# Update 21: own the full bar height (rows 0..31 = exactly two 16-byte data-cache
# lines per 240-byte column). Writing only rows 4..27 made the flush write back
# stale cached bytes in rows 0..3 / 28..31 (garbage stripes over the bar).
Y_MIN, Y_MAX = 0, 31
X0, Y0 = 4, 8                 # text origin
SCALE, ADVANCE = 2, 12
MAX_CHARS = (X_MAX+1-X0+SCALE)//ADVANCE      # 15
SHORT_NAMES = {6: 'CRACKED', 8: 'ERROR DIFF 1D'}
FILTER_STATE = 0x020892c8+97  # stock filter category (1 = colour filter), index 1..5
FILTER_MODE0 = 11             # dither off + colour filter k -> label of pseudo-mode 11+k
FLUSH = BIAS+0x29790


def offset(x, y): return (319-x)*240+y


def reference(layer, enabled, mode, names, category=0, index=0):
    """Host oracle on a 76800-byte layer; names[mode] is the label text."""
    out = bytearray(layer)
    if not enabled and category == 1 and 1 <= index <= 5:
        enabled, mode = 1, FILTER_MODE0+index
    for x in range(X_MIN, X_MAX+1):
        for y in range(Y_MIN, Y_MAX+1): out[offset(x, y)] = BAR
    if enabled and 0 <= mode < len(names) and names[mode]:
        for i, ch in enumerate(names[mode][:MAX_CHARS]):
            for c, bits in enumerate(GLYPHS.get(ch, GLYPHS[' '])):
                for r in range(7):
                    if bits >> r & 1:
                        for dx in range(SCALE):
                            for dy in range(SCALE):
                                out[offset(X0+i*ADVANCE+c*SCALE+dx, Y0+r*SCALE+dy)] = WHITE
    return bytes(out)


def _section(a, tag, origin, base, max_mode, allocation_size):
    """Draw into the buffer in r2 (validated here); r4 enabled, r5 mode.
    Clobbers r3..r8, r12..r20 and calls the stock flush. Ends at 'skip'+tag."""
    imm = a.immediate
    imm(0x29, 13, 2, 31); imm(0x2f, 1, 13, 0); a.branch(4, 'skip'+tag)
    a.const(13, 0x02090000); a.compare(2, 13, 4); a.branch(4, 'skip'+tag)
    a.const(13, 0x021ff000-BUFFER_BYTES); a.compare(2, 13, 2); a.branch(4, 'skip'+tag)
    a.const(13, base+allocation_size); a.compare(2, 13, 3); a.branch(4, 'disjoint'+tag)
    a.const(13, BUFFER_BYTES); a.alu(13, 2, 13); a.const(14, base)
    a.compare(13, 14, 2); a.branch(4, 'skip'+tag)
    a.label('disjoint'+tag)
    imm(0x27, 15, 0, BAR)
    a.const(16, (319-X_MAX)*240+Y_MIN); a.alu(16, 2, 16)
    imm(0x27, 17, 0, X_MAX-X_MIN+1)
    a.label('clear_row'+tag); imm(0x27, 18, 0, 0)
    a.label('clear_px'+tag); a.alu(19, 16, 18); a.store(15, 19, 0, op=0x36)
    imm(0x27, 18, 18, 1); imm(0x2f, 1, 18, Y_MAX-Y_MIN+1); a.branch(4, 'clear_px'+tag)
    imm(0x27, 16, 16, 240); imm(0x27, 17, 17, -1); imm(0x2f, 1, 17, 0); a.branch(4, 'clear_row'+tag)
    imm(0x2f, 1, 4, 0); a.branch(4, 'enabled'+tag)
    # dither off: name of the selected gray filter curve (stock colour filter 1..5)
    a.const(13, FILTER_STATE); imm(0x23, 5, 13, 0); imm(0x2f, 1, 5, 1); a.branch(4, 'flush'+tag)
    imm(0x23, 5, 13, 1); imm(0x2f, 0, 5, 0); a.branch(4, 'flush'+tag)
    imm(0x2f, 2, 5, 5); a.branch(4, 'flush'+tag)
    imm(0x27, 5, 5, FILTER_MODE0); a.branch(0, 'draw'+tag)
    a.label('enabled'+tag)
    imm(0x2f, 2, 5, max_mode); a.branch(4, 'flush'+tag)
    a.label('draw'+tag)
    imm(0x27, 6, 5, 1); imm(0x2e, 6, 6, 5); a.const(13, base+LABELS); a.alu(6, 6, 13)
    a.const(7, base+FONT); imm(0x27, 15, 0, WHITE)
    a.const(8, offset(X0, Y0)); a.alu(8, 2, 8)
    imm(0x27, 20, 0, MAX_CHARS)
    a.label('char'+tag)
    imm(0x23, 13, 6, 0); imm(0x2f, 0, 13, 0); a.branch(4, 'flush'+tag)
    imm(0x2f, 2, 13, 127); a.branch(4, 'advance'+tag)
    imm(0x27, 14, 0, 5); a.alu(13, 13, 14, 0x306); a.alu(13, 7, 13)
    imm(0x27, 14, 0, 0); imm(0x2a, 16, 8, 0)
    a.label('column'+tag)
    imm(0x23, 17, 13, 0); imm(0x2a, 18, 16, 0)
    a.label('bit'+tag)
    imm(0x29, 19, 17, 1); imm(0x2f, 0, 19, 0); a.branch(4, 'next_bit'+tag)
    for d in (0, 1, -240, -239): a.store(15, 18, d, op=0x36)
    a.label('next_bit'+tag)
    imm(0x27, 18, 18, SCALE); imm(0x2e, 17, 17, 0x41); imm(0x2f, 1, 17, 0); a.branch(4, 'bit'+tag)
    imm(0x27, 13, 13, 1)
    imm(0x27, 16, 16, -240*SCALE); imm(0x27, 14, 14, 1); imm(0x2f, 1, 14, 5); a.branch(4, 'column'+tag)
    a.label('advance'+tag)
    imm(0x27, 8, 8, -240*ADVANCE); imm(0x27, 6, 6, 1)
    imm(0x27, 20, 20, -1); imm(0x2f, 1, 20, 0); a.branch(4, 'char'+tag)
    a.label('flush'+tag)
    a.const(3, (319-X_MAX)*240); a.alu(3, 2, 3); a.const(4, (X_MAX-X_MIN+1)*240)
    a.call(origin, FLUSH)
    a.label('skip'+tag)


def _descriptor_ok(a, fail):
    """r12 = DESCRIPTOR; checks dims and both buffer sizes."""
    imm = a.immediate
    a.const(12, DESCRIPTOR)
    imm(0x21, 13, 12, 8); a.const(14, DIMS); a.compare(13, 14, 1); a.branch(4, fail)
    a.const(14, BUFFER_BYTES)
    for off in (0x14, 0x1c):
        imm(0x21, 13, 12, off); a.compare(13, 14, 1); a.branch(4, fail)


def osd_label(base, max_mode, allocation_size):
    """Same call ABI as preview_labels.overlay: r3 (unused), r4 enabled, r5 mode.
    Preserves every register; returns r11 = 0. Draws into both overlay buffers."""
    a = Code(); imm = a.immediate; origin = base+LABEL_CODE
    saved = (2, 3, 4, 5, 6, 7, 8, 9, 12, 13, 14, 15, 16, 17, 18, 19, 20)
    frame = len(saved)*4
    imm(0x27, 1, 1, -frame)
    for i, r in enumerate(saved): a.store(r, 1, i*4)
    _descriptor_ok(a, 'done')
    imm(0x21, 13, 12, 0x20); imm(0x21, 14, 12, 0x10); a.compare(13, 14, 0); a.branch(4, 'in_use_ok')
    imm(0x21, 14, 12, 0x18); a.compare(13, 14, 1); a.branch(4, 'done')
    a.label('in_use_ok')
    for k, off in enumerate((0x10, 0x18)):
        a.const(12, DESCRIPTOR); imm(0x21, 2, 12, off)
        imm(0x21, 4, 1, saved.index(4)*4); imm(0x21, 5, 1, saved.index(5)*4)
        _section(a, str(k), origin, base, max_mode, allocation_size)
    a.label('done')
    for i, r in enumerate(saved): imm(0x21, r, 1, i*4)
    imm(0x27, 1, 1, frame); imm(0x27, 11, 0, 0); a.emit(0x44004800)
    code = a.finish()
    if len(code) > FLIP_WRAP-LABEL_CODE: raise ValueError('osd label overflow')
    return code


# Update 18: the stock UI re-renders its overlay into the spare buffer and flips
# every second (clock refresh), so a per-frame label was missing between the flip
# and the next preview frame. Hook the single flip call (flash 0x61d0 in 0x619c:
# jal 0x3783c with r3 = layer 0, r4 = buffer to show) and paint the label into
# that buffer first, in camera mode only.
FLIP_CALL = 0x61d0
FLIP_TARGET = 0x3783c
FLIP_WRAP = LABEL_CODE+0x500
MODE_WORD = 0x02085e9c
DMA_WAIT = BIAS+0x29c3c           # waits for / retires the 0x29ca0 DMA copy; clobbers r3..r5


def flip_wrapper(base, max_mode, allocation_size, state):
    from build_gray_candidate import branch
    import struct
    a = Code(); imm = a.immediate; origin = base+FLIP_WRAP
    # Stands in for the call `jal 0x3783c` at 0x61d0: keep the flip arguments
    # (r3..r8), the link register and every callee-saved register this code or
    # _section touches; caller-saved temporaries are dead after that call (the
    # caller only reloads r11 from its stack and returns). Update 24 (size).
    saved = (2, 3, 4, 5, 6, 7, 8, 9, 10, 14, 16, 18, 20)
    frame = len(saved)*4
    imm(0x27, 1, 1, -frame)
    for i, r in enumerate(saved): a.store(r, 1, i*4)
    a.const(12, MODE_WORD); imm(0x21, 12, 12, 0); imm(0x2f, 1, 12, 3); a.branch(4, 'tail')
    imm(0x2f, 1, 3, 0); a.branch(4, 'tail')                       # layer 0 only
    _descriptor_ok(a, 'tail')
    imm(0x21, 13, 12, 0x10); a.compare(4, 13, 0); a.branch(4, 'buffer_ok')
    imm(0x21, 13, 12, 0x18); a.compare(4, 13, 1); a.branch(4, 'tail')
    a.label('buffer_ok')
    # Update 22: the stock present path copies its UI canvas into this buffer with
    # the asynchronous DMA memcpy 0x29ca0 and flips without waiting; painting now
    # would be overwritten by the tail of that copy (left of the screen). Wait for
    # it with the stock wait routine first (no-op when no copy is in flight).
    imm(0x21, 4, 1, saved.index(4)*4)
    a.call(origin, DMA_WAIT)
    imm(0x21, 4, 1, saved.index(4)*4)
    imm(0x2a, 2, 4, 0)
    a.const(12, state); imm(0x21, 4, 12, 0); imm(0x21, 5, 12, 4)   # preview enabled/mode
    _section(a, 'f', origin, base, max_mode, allocation_size)
    a.label('tail')
    for i, r in enumerate(saved): imm(0x21, r, 1, i*4)
    imm(0x27, 1, 1, frame)
    code = a.finish()
    code += branch(origin+len(code), BIAS+FLIP_TARGET)               # l.j into the stock flip
    if len(code) > 0x300: raise ValueError('flip wrapper overflow')
    return code


def flip_hook(original, base):
    import struct
    from build_gray_candidate import branch
    old = original[FLIP_CALL:FLIP_CALL+4]
    expected = struct.unpack('<I', branch(BIAS+FLIP_CALL, BIAS+FLIP_TARGET))[0] | 1 << 26
    if old != struct.pack('<I', expected): raise ValueError('unexpected overlay flip call')
    new = struct.unpack('<I', branch(BIAS+FLIP_CALL, base+FLIP_WRAP))[0] | 1 << 26
    return {'address': BIAS+FLIP_CALL, 'original': old.hex(), 'replacement': struct.pack('<I', new).hex(),
            'label': 'overlay flip: paint mode label into the buffer being shown (camera mode)'}
