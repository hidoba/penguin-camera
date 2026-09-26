"""Frame JPEG encoder with a spatial transparency mask (used by frames.py).

Stock composition (flash 0x40fcc..0x41104) copies a frame pixel over the photo
only when its decoded Y > 27; Y <= 27 lets the photo through. A plain luminance
rule would make genuinely black artwork (text, eyes, dark details) transparent
and JPEG noise would speckle it. Instead:

- transparent = the given mask (a PNG's alpha channel), or else connected
  (4-neighbour) regions of source Y < 15 with at least MIN_BACKGROUND pixels,
  plus tiny enclosed specks inside them; encoded as
  exact Y=0, Cb=Cr=128;
- everything else is artwork: Y' = FLOOR + round(Y * (255-FLOOR) / 255), with
  source chroma, so black artwork stays near-black but clearly visible;
- after JPEG encoding, any artwork pixel decoding below SAFE is raised in the
  master and re-encoded (bounded closed loop), keeping a margin over 27 for
  decoder differences. Slot sizes/dimensions/sampling are unchanged.
"""
import io
from collections import deque
from PIL import Image
from prepare_release_artwork import fit_jpeg

CUTOFF = 15
MIN_BACKGROUND = 2000
MAX_SPECK = 24
FLOOR = 40
TRANSPARENT_MAX = 27
SAFE = 34
ITERATIONS = 12


def luma(rgb):
    return bytes((19595*r+38470*g+7471*b+32768) >> 16 for r, g, b in rgb.getdata())


def components(mask, w, h):
    label = [-1]*(w*h); sizes = []
    for start in range(w*h):
        if not mask[start] or label[start] >= 0: continue
        n = len(sizes); label[start] = n; queue = deque([start]); size = 0
        while queue:
            p = queue.popleft(); size += 1; x, y = p % w, p // w
            for q, ok in ((p-1, x > 0), (p+1, x < w-1), (p-w, y > 0), (p+w, y < h-1)):
                if ok and mask[q] and label[q] < 0: label[q] = n; queue.append(q)
        sizes.append(size)
    return label, sizes


def background_mask(y, w, h):
    dark = [v < CUTOFF for v in y]
    label, sizes = components(dark, w, h)
    bg = [l >= 0 and sizes[l] >= MIN_BACKGROUND for l in label]
    # Enclosed specks of JPEG noise inside the background are background too.
    label, sizes = components([not b for b in bg], w, h)
    touches = [False]*len(sizes)
    for p, l in enumerate(label):
        if l < 0: continue
        x, yy = p % w, p // w
        if x in (0, w-1) or yy in (0, h-1): touches[l] = True
    return [b or (label[p] >= 0 and sizes[label[p]] <= MAX_SPECK and not touches[label[p]])
            for p, b in enumerate(bg)]


def decoded_y(payload):
    with Image.open(io.BytesIO(payload)) as im:
        im.draft('YCbCr', im.size); im.load()
        if im.mode != 'YCbCr': raise ValueError('expected native YCbCr decode')
        return im.getchannel('Y').tobytes()


def half_y(payload):
    with Image.open(io.BytesIO(payload)) as im:
        im.draft('YCbCr', (im.width//2, im.height//2)); im.load()
        return im.getchannel('Y').tobytes(), im.size


def build_frame(rgb, old_jpeg, budget, mask=None):
    """mask: transparent pixels (e.g. from an alpha channel); None = black-region rule."""
    w, h = rgb.size
    source_y = luma(rgb)
    _, cb, cr = rgb.convert('YCbCr').split()
    cb, cr = bytearray(cb.tobytes()), bytearray(cr.tobytes())
    bg = background_mask(source_y, w, h) if mask is None else list(mask)
    target = bytearray(0 if b else FLOOR+(v*(255-FLOOR)+127)//255 for v, b in zip(source_y, bg))
    for p, b in enumerate(bg):
        if b: cb[p] = cr[p] = 128
    master = bytearray(target); history = []
    for iteration in range(ITERATIONS):
        image = Image.merge('YCbCr', [Image.frombytes('L', (w, h), bytes(c)) for c in (master, cb, cr)])
        payload, quality, sampling = fit_jpeg(image, old_jpeg, budget)
        dec = decoded_y(payload)
        low = [p for p in range(w*h) if not bg[p] and dec[p] < SAFE]
        history.append({'quality': quality, 'bytes': len(payload), 'artwork_below_safe': len(low),
                        'artwork_le_27': sum(dec[p] <= TRANSPARENT_MAX for p in low)})
        if not low: break
        for p in low: master[p] = min(255, master[p]+(SAFE-dec[p])+2)
    half, size = half_y(payload)
    audit = {
        'artwork_pixels': w*h-sum(bg), 'transparent_pixels': sum(bg),
        'artwork_decoded_Y_le_27': sum(1 for p in range(w*h) if not bg[p] and dec[p] <= TRANSPARENT_MAX),
        'artwork_decoded_min': min(dec[p] for p in range(w*h) if not bg[p]),
        'transparent_decoded_Y_gt_27': sum(1 for p in range(w*h) if bg[p] and dec[p] > TRANSPARENT_MAX),
        'half_scale_decode_size': list(size),
        'half_scale_artwork_le_27': sum(1 for yy in range(size[1]) for x in range(size[0])
                                       if half[yy*size[0]+x] <= TRANSPARENT_MAX and
                                       not any(bg[(2*yy+dy)*w+2*x+dx] for dy in (0, 1) for dx in (0, 1))),
        'closed_loop': history, 'decoder': 'host libjpeg; not hardware-decoder validation'}
    return image, payload, quality, sampling, bg, bytes(target), audit


