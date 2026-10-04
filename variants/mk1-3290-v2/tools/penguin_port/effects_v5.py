"""Vendored penguin camera math/native generator. Adaptations: MK1 RAM bounds; explicit UV input for orange; larger sensor width. No hardware access."""
import math, struct, json
from pathlib import Path
from mk1_native import Code

def bayer8_thresholds():
    """Identical to the resident mode-0 table: white iff pixel >= value."""
    def bayer(x, y):
        q = ((0, 2), (3, 1)); v = 0
        for bit in range(3): v = 4*v + q[(y >> bit) & 1][(x >> bit) & 1]
        return v
    return bytes(((2*bayer(x, y)+1)*255)//128+1 for y in range(8) for x in range(8))

HALFTONE_TAPS = {6: (1, 4, 6, 4, 1), 5: (1, 7, 16, 7, 1)}

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
