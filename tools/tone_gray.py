"""Measured tone curve + line-load + heat-history compensation for gray prints.

Model v2 ("input shift", analysis/gray_model_02.json; 129 i1Pro readings from the
raw charts 04+03 and the v1 verification chart, RMS 2.7 L*):
    L*(v, U, C, x) = curve(v + a*U + b*C + c*x)
      v  printer input 0..255          U  line load (sum of 255-v over the line / (255*88))
      C  unit-lines printed before     x  dot across the head (0..383)
Load, heat history and head position act like shifts of the printer input (they
change the energy per dot), so they vanish in saturated black/white and matter
most in the steep midtones. For each dot:
    v = curve^-1(target(g)) - a*U - b*C - c*x,  target(g) = black + (paper-black)*L*_sRGB(g)/100
U comes from the previous printed line (0.125 mm apart), C from everything
printed so far; both are clamped to the measured range. g=0 -> v 0, g=255 -> 255.

Fixed point (1/16 input steps), bit-exact between `reference` and the native routine:
tables TV[256] u16, COL[384] s16 by buffer row r = 383-x.
Print buffer layout: width = feed (w), height = 384, dot x stored at row 383-x.
(v1, the additive-L* model of update 13, was replaced after its verification
chart measured RMS 5.6 L*.)
"""
import json
import struct
from pathlib import Path
from live_preview_patch import Code

from project_paths import GRAY_MODEL as MODEL
UNIT = 255*88
TONE = 0xb380
T_TABLE = 0xb800          # 256 x u16 (TV, 1/16 input units)
COL_TABLE = 0xbc00        # 384 x s16 by buffer row (1/16 input units)
MAX_CODE = T_TABLE-TONE
S_INIT = 2*UNIT           # assumed load of the line before the first one
C_MAX = 2500              # unit-lines
# Update 17: user prefers the old, punchier shadows. Dark grays follow the old
# 190/255 mapping (at typical line load), highlights the perceptual target, with
# a smoothstep blend in input space between these camera-gray limits.
OLD_SCALE = 190/255
DARK_BLEND = (80, 176)
TYPICAL = dict(U=2.2, C=900, x=192)


def srgb_lstar(g):
    c = g/255
    y = c/12.92 if c <= 0.04045 else ((c+0.055)/1.055)**2.4
    f = y**(1/3) if y > 216/24389 else (24389/27*y+16)/116
    return 116*f-16


def tables(model_path=MODEL):
    m = json.loads(Path(model_path).read_text())
    if m.get('model') != 'input_shift': raise ValueError('expected the input-shift model')
    kn, kl = m['knots'], m['knot_L']
    a, b, c = m['a_load'], m['b_hist'], m['c_x']
    black, paper = m['black_L'], m['paper_L']

    def inverse(L):                     # monotone piecewise-linear curve^-1
        if L <= kl[0]: return kn[0]
        if L >= kl[-1]: return kn[-1]
        for (v0, L0), (v1, L1) in zip(zip(kn, kl), zip(kn[1:], kl[1:])):
            if L0 <= L <= L1: return v0 if L1 == L0 else v0+(v1-v0)*(L-L0)/(L1-L0)
    typ = a*TYPICAL['U']+b*TYPICAL['C']+c*TYPICAL['x']

    def curve(v):
        if v <= kn[0]: return kl[0]
        if v >= kn[-1]: return kl[-1]
        for (v0, L0), (v1, L1) in zip(zip(kn, kl), zip(kn[1:], kl[1:])):
            if v0 <= v <= v1: return L0+(L1-L0)*(v-v0)/(v1-v0)

    def weight(g):
        g0, g1 = DARK_BLEND
        if g <= g0: return 1.0
        if g >= g1: return 0.0
        t = (g-g0)/(g1-g0); return 1-(3*t*t-2*t**3)
    eff = [weight(g)*(OLD_SCALE*g+typ)+(1-weight(g))*inverse(black+(paper-black)*srgb_lstar(g)/100) for g in range(256)]
    TV = [round(16*e) for e in eff]
    TV[0] = 0                             # pure black: full heat
    TV[255] = 0xffff                      # pure white: no heat
    col = [round(16*c*(383-r)) for r in range(384)]
    lo, hi = m['U_range']
    return {'TV': TV, 'COL': col,
            'Ka': round(16*a/UNIT*(1 << 16)), 'Kb': round(16*(-b)/UNIT*(1 << 28)),
            'S_min': round(max(1.0, lo)*UNIT), 'S_max': round(hi*UNIT), 'C_max': C_MAX*UNIT,
            'black_L': black, 'paper_L': paper, 'model': {k: m[k] for k in ('a_load', 'b_hist', 'c_x', 'rms')},
            'target_L': [curve(e) for e in eff]}   # predicted L* at typical line conditions


def line_term(t, s_prev, c_dots):
    """Input shift of this line in 1/16 steps: a*U + b*C (b < 0)."""
    s = min(max(s_prev, t['S_min']), t['S_max'])
    c = min(c_dots, t['C_max'])
    return ((s*t['Ka']) >> 16) - (((c >> 8)*t['Kb']) >> 20)


def reference(buf, w, h, t):
    out = bytearray(buf[:w*h])
    s_prev, c_dots = S_INIT, 0
    for y in range(w):
        lt = line_term(t, s_prev, c_dots); s = 0
        for r in range(h):
            i = r*w+y
            need = t['TV'][out[i]]-lt-t['COL'][r]
            v = min((max(need, 0)+8) >> 4, 255)
            out[i] = v; s += 255-v
        c_dots += s; s_prev = s
    return bytes(out)


def blob_tables(t):
    return {T_TABLE: struct.pack('<256H', *t['TV']), COL_TABLE: struct.pack('<384h', *t['COL'])}


def native(base, t):
    """r3=buffer, r4=w (feed), r5=h (must be 384; else r11=8, untouched).
    Preserves every register except r11 (0 on success)."""
    a = Code(); imm = a.immediate
    saved = tuple(r for r in range(2, 32) if r not in (9, 11))
    frame = len(saved)*4
    imm(0x27, 1, 1, -frame)
    for i, r in enumerate(saved): a.store(r, 1, i*4)
    imm(0x2f, 1, 5, 384); a.branch(4, 'reject')
    imm(0x2f, 4, 4, 1); a.branch(4, 'reject')
    imm(0x2f, 2, 4, 1024); a.branch(4, 'reject')
    a.const(20, base+T_TABLE); a.const(22, base+COL_TABLE)
    a.const(23, S_INIT); imm(0x27, 24, 0, 0)          # s_prev, c_dots
    a.const(25, t['Ka']); a.const(26, t['Kb'])
    imm(0x27, 7, 0, 0)                                # y
    a.label('line')
    # line term
    a.const(12, t['S_min']); a.compare(23, 12, 4); a.branch(3, 'smin_ok'); imm(0x2a, 23, 12, 0)
    a.label('smin_ok'); a.const(12, t['S_max']); a.compare(23, 12, 2); a.branch(3, 'smax_ok'); imm(0x2a, 23, 12, 0)
    a.label('smax_ok'); a.alu(27, 23, 25, 0x306); imm(0x2e, 27, 27, 0x50)       # (s*Ka)>>16
    imm(0x2a, 12, 24, 0); a.const(13, t['C_max']); a.compare(12, 13, 2); a.branch(3, 'cmax_ok'); imm(0x2a, 12, 13, 0)
    a.label('cmax_ok'); imm(0x2e, 12, 12, 0x48); a.alu(12, 12, 26, 0x306); imm(0x2e, 12, 12, 0x54)   # ((c>>8)*Kb)>>20
    a.alu(27, 27, 12, 2)                                                        # r27 = line term
    # dots
    a.alu(15, 3, 7); imm(0x27, 8, 0, 0); imm(0x27, 28, 0, 0)                  # ptr, r, s
    a.label('dot')
    imm(0x23, 12, 15, 0); imm(0x2e, 12, 12, 1); a.alu(12, 20, 12); imm(0x25, 12, 12, 0)   # T[g]
    a.alu(12, 12, 27, 2)
    imm(0x2e, 13, 8, 1); a.alu(13, 22, 13); imm(0x25, 13, 13, 0)         # COL[r] (s16)
    imm(0x2e, 13, 13, 0x10); imm(0x2e, 13, 13, 0x90)                                     # sign-extend 16
    a.alu(12, 12, 13, 2)
    imm(0x2f, 11, 12, 0); a.branch(4, 'nonneg'); imm(0x27, 12, 0, 0)
    a.label('nonneg'); imm(0x27, 12, 12, 8); imm(0x2e, 12, 12, 0x44)          # (need+8)>>4
    imm(0x2f, 5, 12, 255); a.branch(4, 'fits'); imm(0x27, 12, 0, 255)
    a.label('fits')                                                            # v
    a.store(12, 15, 0, op=0x36)
    imm(0x27, 13, 0, 255); a.alu(13, 13, 12, 2); a.alu(28, 28, 13)            # s += 255-v
    a.alu(15, 15, 4); imm(0x27, 8, 8, 1); imm(0x2f, 1, 8, 384); a.branch(4, 'dot')
    a.alu(24, 24, 28); imm(0x2a, 23, 28, 0)                                    # c += s; s_prev = s
    imm(0x27, 7, 7, 1); a.compare(7, 4, 4); a.branch(4, 'line')
    imm(0x27, 11, 0, 0); a.branch(0, 'restore')
    a.label('reject'); imm(0x27, 11, 0, 8)
    a.label('restore')
    for i, r in enumerate(saved): imm(0x21, r, 1, i*4)
    imm(0x27, 1, 1, frame); a.emit(0x44004800)
    code = a.finish()
    if len(code) > MAX_CODE: raise ValueError('tone routine overflow')
    return code
