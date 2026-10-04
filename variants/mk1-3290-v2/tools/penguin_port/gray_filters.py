"""Vendored penguin camera math/native generator. Adaptations: MK1 RAM bounds; explicit UV input for orange; larger sensor width. No hardware access."""
import math, struct, json
from pathlib import Path
from mk1_native import Code

CURVE_STATE = 0x4ec0

CURVE_COUNT = 4

AUTO, FLAT = 0, 1

FRAME_TRANSPARENT_MAX = 27

MIN_SAMPLES = 256

LUTS = 0x7600

MIN_RANGE = 64

MAX_COUNT = 1536*1536

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
    a.alu(15, 3, 4); a.const(12, 0x027fec00); a.compare(15, 12, 2); a.branch(4, 'done')
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
