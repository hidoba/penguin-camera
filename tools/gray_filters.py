"""Gray preview and the OK-button tone curves (updates 24/25).

Update 25: the curves are our own setting, cycled by the OK button in camera
mode (idle_reset trampoline), independent of the stock filters and of dithering:

  CURVE_STATE 0 off, 1 AUTO LEVELS, 2 MORE CONTRAST, 3 LIFT SHADOWS, 4 TAME HIGHLIGHTS

  AUTO LEVELS      per image: 1/128 low/high tails clipped, >= 64 input range
  MORE CONTRAST    sigmoid S-curve (a = 5)
  LIFT SHADOWS     x + 1.2 x (1-x)^3
  TAME HIGHLIGHTS  x - 1.2 (1-x) x^3

The curve is applied to the orange-filter gray (color_gray.ORANGE) everywhere:
  - normal preview: orange, curve, neutral chroma (screen shows the print gray)
  - dither preview: orange + curve before the dither kernel (`pre=`)
  - print (all modes): orange + curve in the transform's `before=` hook, then the
    dither kernel or the measured tone compensation.

Stock colour filters (down key in normal mode, G = 0x020892c8: G+97 category,
G+98 colour 1..5) were sensor register effects (0x4a3e4 -> regs 0xBA/0xBB).
Hook: sensor effect always none. Update 25 also removes colour filters from the
stock up/down cycle (none <-> kaleidoscope 0), so they are never selected.
"""
import math
import struct
from live_preview_patch import Code
from ui_trace_patch import BIAS
from or1k_subset import branch

CURVE_STATE = 0x4ec0                  # payload word (update 32): 0 AUTO (boot default), 1 FLAT, 2..4 LUT curves
CURVE_COUNT = 4                       # highest state; OK cycles 0..CURVE_COUNT
CURVE_NAMES = ('AUTO LEVELS', 'FLAT CURVE', 'MORE CONTRAST', 'LIFT SHADOWS', 'TAME HIGHLIGHTS')
AUTO, FLAT = 0, 1
LABEL_MODE0 = 11                      # curve state s uses the label slot of pseudo-mode 11+s+1 (slot 12+s)
DISPLAY_LUT = 0x7900                  # update 32: complete copy of the last preview auto LUT (curve icon)
# Update 34: with a stock frame active, AUTO LEVELS ignores frame pixels. The stock
# compositor (0x40f58, called at 0x948c just before our preview hook) copies a frame
# pixel where the frame's own luma is > 27; its state block holds the frame image.
FRAME_STATE = 0x0208a974              # +0 active (1), +28 u16 width, +30 u16 height, +36 frame Y
FRAME_TRANSPARENT_MAX = 27
FILTER_CATEGORY = 0x020892c8+97       # stock category byte: 3 = frame
MIN_SAMPLES = 256                     # fewer photo samples -> leave the picture unchanged
CODE = 0x9200                         # free since update 20 (old sliced halftone)
CODE_END = 0x9800
LUTS = 0x7600                         # 3 x 256 static curves (MORE, LIFT, TAME)
PREVIEW_AUTO = 0x7a00                 # u16 hist[256] + u8 lut[256]
PRINT_AUTO = 0x7d00
DATA_END = 0x8000                     # 0x8000 = preview scratch
PREVIEW_STEP, PRINT_STEP = 4, 8       # auto-levels sampling (<= 49152 samples, fits u16)
MIN_RANGE = 64
MAX_COUNT = 1024*1024
SENSOR_EFFECT = 0x4a3f4               # l.andi r2,r3,0xff in 0x4a3e4(effect)
SENSOR_EFFECT_OLD, SENSOR_EFFECT_NEW = 0xa44300ff, 0xa44000ff   # -> l.andi r2,r0,0xff
# Stock filter cycle without colour filters (update 25).
DOWN_TO_FRAMES = 0xbfb0               # update 28b: none -> frame 0
UP_FRAMES_TO_NONE = 0xc3bc            # frame 0 -> none (update 32: l.j to up_exit_stub)
DOWN_ZOOM, UP_ZOOM = 0xc12c, 0xc3f4   # update 32: hold-to-zoom branches skipped
UP_SKIP = 0xc34c                      # kaleidoscope 0 -> colour 5 ; now -> 0xc2fc: colour exit with idx 0


def curves():
    def s(t): return 1/(1+math.exp(-t))
    a = 5.0
    fs = (lambda x: (s(a*(x-.5))-s(-a/2))/(s(a/2)-s(-a/2)),
          lambda x: x+1.2*x*(1-x)**3,
          lambda x: x-1.2*(1-x)*x**3)
    return [bytes(min(255, max(0, round(255*f(v/255)))) for v in range(256)) for f in fs]


def auto_lut(samples):
    hist = [0]*256
    for v in samples: hist[v] += 1
    t = len(samples) >> 7
    lo, cum = 0, 0
    while lo < 255:
        cum += hist[lo]
        if cum > t: break
        lo += 1
    hi, cum = 255, 0
    while hi > 0:
        cum += hist[hi]
        if cum > t: break
        hi -= 1
    if hi-lo < MIN_RANGE:
        lo = min(max(((lo+hi) >> 1)-MIN_RANGE//2, 0), 255-MIN_RANGE); hi = lo+MIN_RANGE
    rng = hi-lo
    return bytes(0 if v <= lo else 255 if v >= hi else (v-lo)*255//rng for v in range(256))


def apply_reference(y, curve, step, mask=None, lut=None):
    """y: luma bytes (len % 4 == 0). mask: frame luma of the same geometry (pixels
    > 27 are frame, not photo, and are left out of the AUTO histogram); lut: use this
    AUTO table instead of measuring (prints with a frame). Returns the curved copy."""
    y = bytearray(y)
    if curve == FLAT or not 0 <= curve <= CURVE_COUNT or len(y) % 4 or not y: return y
    if len(y) > 65535*step: return y
    if curve == AUTO:
        if lut is None:
            samples = [y[i] for i in range(0, len(y), step) if mask is None or mask[i] <= FRAME_TRANSPARENT_MAX]
            if len(samples) < MIN_SAMPLES: return y
            lut = auto_lut(samples)
    else:
        lut = curves()[curve-2]
    return bytearray(lut[v] for v in y)


def orange_curve_reference(buf, w, h, curve, step, mask=None, lut=None):
    from color_gray import reference as orange
    return bytes(apply_reference(orange(buf, w, h), curve, step, mask, lut))


def preview_reference(frame, curve, mask=None):
    """Normal preview: 320x240 Y + interleaved chroma -> gray Y + neutral chroma."""
    n = 320*240
    return orange_curve_reference(frame, 320, 240, curve, PREVIEW_STEP, mask)+bytes([128])*(n//2)+bytes(frame[n+n//2:])


CURVE_SAVED = tuple(range(12, 26))


def curve_sub(base, origin):
    """r3 = luma, r4 = byte count, r5 = auto scratch (hist u16[256] + lut), r6 = sample
    step, r7 = where to copy a finished auto LUT (0 = nowhere), r8 = frame mask (frame
    luma, same geometry; 0 = none; 1 = use the AUTO LUT at r7 as given). Applies the
    selected curve in place. Preserves every register."""
    a = Code(); imm = a.immediate
    imm(0x27, 1, 1, -len(CURVE_SAVED)*4)
    for i, r in enumerate(CURVE_SAVED): a.store(r, 1, i*4)
    a.const(12, base+CURVE_STATE); imm(0x21, 13, 12, 0)
    imm(0x2f, 0, 13, FLAT); a.branch(4, 'done'); imm(0x2f, 2, 13, CURVE_COUNT); a.branch(4, 'done')
    imm(0x2f, 0, 4, 0); a.branch(4, 'done')
    imm(0x29, 12, 4, 3); imm(0x2f, 1, 12, 0); a.branch(4, 'done')
    a.const(12, MAX_COUNT); a.compare(4, 12, 2); a.branch(4, 'done')
    a.const(12, 65535); a.alu(12, 12, 6, 0x306); a.compare(4, 12, 2); a.branch(4, 'done')   # u16 histogram
    a.const(12, 0x02000000); a.compare(3, 12, 4); a.branch(4, 'done')
    a.alu(15, 3, 4); a.const(12, 0x02200000); a.compare(15, 12, 2); a.branch(4, 'done')
    imm(0x2f, 0, 13, AUTO); a.branch(4, 'auto')
    imm(0x27, 13, 13, -2); imm(0x2e, 13, 13, 8); a.const(22, base+LUTS); a.alu(22, 22, 13)
    a.branch(0, 'apply')
    # auto levels: u16 histogram of every step-th byte
    a.label('auto')
    imm(0x2f, 0, 8, 1); a.branch(4, 'given')
    imm(0x2a, 23, 5, 0); imm(0x27, 22, 23, 0x200)                             # hist, lut
    imm(0x27, 12, 0, 0)
    a.label('clear'); a.alu(13, 23, 12); a.store(0, 13, 0)
    imm(0x27, 12, 12, 4); imm(0x2f, 1, 12, 0x200); a.branch(4, 'clear')
    imm(0x2a, 12, 3, 0); imm(0x27, 16, 0, 0)                                  # p, n (r15 = end)
    a.alu(25, 8, 3, 2)                                                         # mask - luma
    a.label('sample')
    imm(0x2f, 0, 8, 0); a.branch(4, 'take')
    a.alu(13, 12, 25); imm(0x23, 13, 13, 0); imm(0x2f, 2, 13, FRAME_TRANSPARENT_MAX); a.branch(4, 'skip')
    a.label('take')
    imm(0x23, 13, 12, 0); imm(0x2e, 13, 13, 1); a.alu(13, 23, 13)
    imm(0x25, 14, 13, 0); imm(0x27, 14, 14, 1); a.store(14, 13, 0, op=0x37)
    imm(0x27, 16, 16, 1)
    a.label('skip')
    a.alu(12, 12, 6); a.compare(12, 15, 4); a.branch(4, 'sample')
    imm(0x2f, 4, 16, MIN_SAMPLES); a.branch(4, 'done')                        # too few photo pixels
    imm(0x2e, 16, 16, 0x47)                                                    # t = n >> 7
    imm(0x27, 17, 0, 0); imm(0x27, 18, 0, 0)                                   # lo, cum
    a.label('lo')
    imm(0x2e, 13, 17, 1); a.alu(13, 23, 13); imm(0x25, 13, 13, 0); a.alu(18, 18, 13)
    a.compare(18, 16, 2); a.branch(4, 'lo_done')
    imm(0x27, 17, 17, 1); imm(0x2f, 4, 17, 255); a.branch(4, 'lo')
    a.label('lo_done')
    imm(0x27, 19, 0, 255); imm(0x27, 18, 0, 0)                                 # hi, cum
    a.label('hi')
    imm(0x2e, 13, 19, 1); a.alu(13, 23, 13); imm(0x25, 13, 13, 0); a.alu(18, 18, 13)
    a.compare(18, 16, 2); a.branch(4, 'hi_done')
    imm(0x27, 19, 19, -1); imm(0x2f, 2, 19, 0); a.branch(4, 'hi')
    a.label('hi_done')
    a.alu(13, 19, 17, 2); imm(0x2f, 11, 13, MIN_RANGE); a.branch(4, 'range_ok')   # sfgesi
    a.alu(17, 17, 19); imm(0x2e, 17, 17, 0x41); imm(0x27, 17, 17, -(MIN_RANGE//2))
    imm(0x2f, 11, 17, 0); a.branch(4, 'lo_pos'); imm(0x27, 17, 0, 0)
    a.label('lo_pos')
    imm(0x2f, 13, 17, 255-MIN_RANGE); a.branch(4, 'lo_max'); imm(0x27, 17, 0, 255-MIN_RANGE)
    a.label('lo_max'); imm(0x27, 19, 17, MIN_RANGE)
    a.label('range_ok')
    a.alu(24, 19, 17, 2)                                                       # range
    imm(0x27, 12, 0, 0); imm(0x27, 13, 0, 0); imm(0x27, 14, 0, 0)              # v, out, rem
    a.label('lut')
    imm(0x27, 25, 0, 0); a.compare(12, 17, 5); a.branch(4, 'put')             # sfleu v,lo
    imm(0x27, 25, 0, 255); a.compare(12, 19, 3); a.branch(4, 'put')           # sfgeu v,hi
    imm(0x27, 14, 14, 255)
    a.label('dda')
    a.compare(14, 24, 4); a.branch(4, 'dda_done')
    a.alu(14, 14, 24, 2); imm(0x27, 13, 13, 1); a.branch(0, 'dda')
    a.label('dda_done'); imm(0x2a, 25, 13, 0)
    a.label('put'); a.alu(16, 22, 12); a.store(25, 16, 0, op=0x36)
    imm(0x27, 12, 12, 1); imm(0x2f, 4, 12, 256); a.branch(4, 'lut')
    # finished LUT -> optional copy (curve icon never sees a half-built table)
    imm(0x2f, 0, 7, 0); a.branch(4, 'apply')
    imm(0x27, 12, 0, 0)
    a.label('copy'); a.alu(13, 22, 12); imm(0x21, 13, 13, 0); a.alu(16, 7, 12); a.store(13, 16, 0)
    imm(0x27, 12, 12, 4); imm(0x2f, 1, 12, 256); a.branch(4, 'copy')
    a.branch(0, 'apply')
    a.label('given'); imm(0x2a, 22, 7, 0)                                      # AUTO LUT supplied
    # apply lut, 4 bytes per iteration
    a.label('apply')
    imm(0x2a, 12, 3, 0)
    a.label('px')
    for k in range(4):
        imm(0x23, 13, 12, k); a.alu(13, 22, 13); imm(0x23, 13, 13, 0); a.store(13, 12, k, op=0x36)
    imm(0x27, 12, 12, 4); a.compare(12, 15, 4); a.branch(4, 'px')
    a.label('done')
    for i, r in enumerate(CURVE_SAVED): imm(0x21, r, 1, i*4)
    imm(0x27, 1, 1, len(CURVE_SAVED)*4); a.emit(0x44004800)
    return a.finish()


def _frame_active(a, fail):
    """r12 = FRAME_STATE when a stock frame is active (category 3, compositor on)."""
    imm = a.immediate
    a.const(12, FRAME_STATE); imm(0x21, 13, 12, 0); imm(0x2f, 1, 13, 1); a.branch(4, fail)
    a.const(13, FILTER_CATEGORY); imm(0x23, 13, 13, 0); imm(0x2f, 1, 13, 3); a.branch(4, fail)


def _entry(base, origin, orange, sub, scratch, step, fixed=None, neutral=False, copy=None, frame=None):
    """orange(r3, r4, r5) then sub(r3, r4*r5, scratch, step, copy) [+ neutral chroma].
    Preserves every register (r11 = 0 when `neutral`, the preview ABI)."""
    a = Code(); imm = a.immediate
    saved = (3, 4, 5, 6, 7, 8, 9, 12, 13, 14)
    imm(0x27, 1, 1, -len(saved)*4)
    for i, r in enumerate(saved): a.store(r, 1, i*4)
    if fixed: imm(0x27, 4, 0, fixed[0]); imm(0x27, 5, 0, fixed[1])
    a.call(origin, orange)
    a.alu(4, 4, 5, 0x306); a.const(5, base+scratch); imm(0x27, 6, 0, step)
    if copy is None: imm(0x27, 7, 0, 0)
    else: a.const(7, base+copy)
    imm(0x27, 8, 0, 0)
    if frame == 'mask':          # preview: the frame image itself is the mask (same 320x240 geometry)
        _frame_active(a, 'no_frame')
        imm(0x25, 13, 12, 28); imm(0x2f, 1, 13, 320); a.branch(4, 'no_frame')
        imm(0x25, 13, 12, 30); imm(0x2f, 1, 13, 240); a.branch(4, 'no_frame')
        imm(0x21, 13, 12, 36); a.const(14, 0x02000000); a.compare(13, 14, 4); a.branch(4, 'no_frame')
        a.const(14, 0x02200000-320*240); a.compare(13, 14, 2); a.branch(4, 'no_frame')
        imm(0x2a, 8, 13, 0)
    elif frame == 'preview_lut': # print: reuse the photo-only AUTO LUT of the last preview frame
        _frame_active(a, 'no_frame')
        imm(0x27, 8, 0, 1); a.const(7, base+DISPLAY_LUT)
    if frame: a.label('no_frame')
    a.call(origin, sub)
    if neutral:
        a.alu(12, 3, 4); a.const(13, 0x80808080); imm(0x2e, 14, 4, 0x43)       # count/8 words
        a.label('chroma'); a.store(13, 12, 0); imm(0x27, 12, 12, 4); imm(0x27, 14, 14, -1)
        imm(0x2f, 1, 14, 0); a.branch(4, 'chroma')
    for i, r in enumerate(saved): imm(0x21, r, 1, i*4)
    imm(0x27, 1, 1, len(saved)*4)
    if neutral: imm(0x27, 11, 0, 0)
    a.emit(0x44004800)
    return a.finish()


def build(base, orange):
    """{offset: bytes} for code and tables, and the entry offsets:
    normal (preview, r3 = frame Y), pre (dither preview, r3/r4/r5), print (r3/r4/r5)."""
    sub = CODE
    code = curve_sub(base, base+sub)
    entries = {}
    off = (sub+len(code)+15) & ~15
    parts = {sub: code}
    for name, scratch, step, fixed, neutral, copy, frame in (
            ('normal', PREVIEW_AUTO, PREVIEW_STEP, (320, 240), True, DISPLAY_LUT, 'mask'),
            ('pre', PREVIEW_AUTO, PREVIEW_STEP, None, False, DISPLAY_LUT, 'mask'),
            ('print', PRINT_AUTO, PRINT_STEP, None, False, None, 'preview_lut')):
        e = _entry(base, base+off, orange, base+sub, scratch, step, fixed, neutral, copy, frame)
        parts[off] = e; entries[name] = off; off = (off+len(e)+15) & ~15
    if off > UP_EXIT_STUB: raise ValueError('gray filter code overflow (reaches the up-exit stub)')
    parts[LUTS] = b''.join(curves())
    parts[DISPLAY_LUT] = bytes(range(256))      # identity until the first auto-levels frame
    return parts, entries


def curve_lut_offsets():
    """Payload offsets of each curve state's 256-byte LUT for the curve icon
    (AUTO = copy of the last complete preview LUT; FLAT = identity, no table)."""
    return {AUTO: DISPLAY_LUT, 2: LUTS, 3: LUTS+0x100, 4: LUTS+0x200}


def _hook(original, address, old, new, label):
    if original[address:address+4] != struct.pack('<I', old): raise ValueError(f'unexpected code at {address:#x}')
    return {'address': BIAS+address, 'original': struct.pack('<I', old).hex(),
            'replacement': struct.pack('<I', new).hex(), 'label': label}


def sensor_effect_hook(original):
    return _hook(original, SENSOR_EFFECT, SENSOR_EFFECT_OLD, SENSOR_EFFECT_NEW,
                 'sensor colour effect 0x4a3e4 always none')


UP_EXIT_STUB = 0x97e0                 # payload stub for "frame 0 -> none" (end of the CODE area; 0x9700 in update 32)


def up_exit_stub(base):
    """Replaces the stock 'frame 0 -> kaleidoscope 5' tail at 0xc3bc (frame already
    switched off): category/stock-state 97, 99, 101 = 0 and the zoom-label redraw
    0xa814 exactly like the stock down-exit (0xc0b4..0xc0c4); then continue at 0xc3cc.
    r2 = G, r18 = handler argument (callee-saved across 0xa814)."""
    a = Code(); imm = a.immediate; origin = base+UP_EXIT_STUB
    a.store(0, 2, 97, op=0x36); a.store(0, 2, 99, op=0x36); a.store(0, 2, 101, op=0x36)
    imm(0x2a, 3, 18, 0); a.call(origin, BIAS+0xa814)
    code = a.finish()
    return code+branch(origin+len(code), BIAS+0xc3cc)


def filter_skip_hooks(original, base):
    """Up/down cycle none <-> frames only (no colour filters, no kaleidoscope), no zoom.
    down: none -> 0xbfb0 jumps to 0xc02c (enter frames at frame 0; +99 already cleared)
    up:   frame 0 -> 0xc3bc jumps to up_exit_stub (none, like the stock down-exit)
    up:   kaleidoscope 0 -> none (0xc34c, update 25; unreachable, kept)
    hold (subtype 1) on up/down: zoom branch skipped (0xc12c / 0xc3f4), always 1.0x."""
    j = lambda a, t: struct.unpack('<I', branch(BIAS+a, BIAS+t))[0]
    return [_hook(original, DOWN_TO_FRAMES, 0x9c600001, j(DOWN_TO_FRAMES, 0xc02c),
                  'down key: none -> frame 0 (skip colour filters and kaleidoscope)'),
            _hook(original, UP_SKIP, 0x9c600001, j(UP_SKIP, 0xc2fc),
                  'up key: kaleidoscope 0 -> none via the colour-filter exit (skip colour filters)'),
            {'address': BIAS+UP_FRAMES_TO_NONE, 'original': struct.pack('<I', 0x9c600002).hex(),
             'replacement': branch(BIAS+UP_FRAMES_TO_NONE, base+UP_EXIT_STUB).hex(),
             'label': 'up key: frame 0 -> none (stub: clear state, redraw zoom label)'},
            _hook(original, DOWN_ZOOM, 0x18400208, j(DOWN_ZOOM, 0xc1b4), 'down key hold: no zoom'),
            _hook(original, UP_ZOOM, 0x18400208, j(UP_ZOOM, 0xc478), 'up key hold: no zoom')]
