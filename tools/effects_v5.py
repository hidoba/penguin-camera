"""Host oracles for the update-05 effects; no device access.

Native kernels in effects_v5_target.py must match these bit for bit.
- Bayer 8x8 + edges: Bayer 8x8 with Sobel contours (non-maximum suppressed),
  dilated 3x3 (about three dots wide) and always drawn black.
- Halftone: gaussf(img, 1) > (cos(pi/3 (x+0.2)) cos(pi/3 (y+0.3)) + 1) * 127,
  with the Gaussian approximated by the separable binomial [1 4 6 4 1]/16
  (variance exactly 1) and clamped (replicated) borders.
- Halftone 4x4 (libdither "Magic 4x4 45") with a gamma-0.8 input lift, folded into its threshold table.
"""
import math
from ditherista_tables import THRESHOLDS

HALFTONE4_GAMMA = 0.8
HALFTONE4_LOWEST = 48   # update 09: first dot level moved down from 73


def bayer8_thresholds():
    """Identical to the resident mode-0 table: white iff pixel >= value."""
    def bayer(x, y):
        q = ((0, 2), (3, 1)); v = 0
        for bit in range(3): v = 4*v + q[(y >> bit) & 1][(x >> bit) & 1]
        return v
    return bytes(((2*bayer(x, y)+1)*255)//128+1 for y in range(8) for x in range(8))


HALFTONE_TAPS = {6: (1, 4, 6, 4, 1), 5: (1, 7, 16, 7, 1)}   # blur sigma = cell/6 (1.0, 0.83)


def halftone_thresholds(period=6):
    """period^2 u32 thresholds: white iff the (sum taps)^2-weighted blur > value
    (Q8 for 6x6, Q10 for 5x5)."""
    scale = sum(HALFTONE_TAPS[period])**2
    out = []
    for y in range(period):
        for x in range(period):
            t = (math.cos(2*math.pi/period*(x+0.2))*math.cos(2*math.pi/period*(y+0.3))+1)*127
            out.append(math.floor(t*scale))
    return out


def halftone4_thresholds(gamma=HALFTONE4_GAMMA, lowest=HALFTONE4_LOWEST):
    """pixel' = round(255 (p/255)^gamma) >= T  <=>  p >= smallest such p.
    The lowest level is then moved to `lowest` (None keeps the gamma value)."""
    def lift(v): return round(255*(v/255)**gamma)
    table = [next(v for v in range(256) if lift(v) >= t) for t in THRESHOLDS]
    if lowest is not None:
        first = min(table)
        if not 0 < lowest <= first: raise ValueError('lowest must stay above 0 and below the next level')
        table = [lowest if v == first else v for v in table]
    return bytes(table)


def halftone(src, w, h, period=6):
    table = halftone_thresholds(period); k = HALFTONE_TAPS[period]
    cx = lambda x: min(max(x, 0), w-1); cy = lambda y: min(max(y, 0), h-1)
    out = bytearray(w*h)
    for y in range(h):
        v = [sum(k[i]*src[cy(y+i-2)*w+x] for i in range(5)) for x in range(w)]
        for x in range(w):
            b = sum(k[i]*v[cx(x+i-2)] for i in range(5))
            out[y*w+x] = 255 if b > table[(y % period)*period+x % period] else 0
    return bytes(out)


def halftone4(src, w, h, table=None):
    table = table or halftone4_thresholds()
    return bytes(255 if src[y*w+x] >= table[(y & 3)*4+(x & 3)] else 0
                 for y in range(h) for x in range(w))
