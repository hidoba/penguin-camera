#!/usr/bin/env python3
"""Rebuild photo frames 006-009 with a spatial transparency mask. Offline only.

Stock composition (flash 0x40fcc..0x41104) copies a frame pixel over the photo
only when its decoded Y > 27; Y <= 27 lets the photo through. The Y=29 curve
kept source Y < 15 unchanged, so genuinely black artwork (text, the penguin's
eyes/head, dark robot parts) stayed transparent, and JPEG noise made letters
speckled. Instead:

- transparent = connected (4-neighbour) regions of source Y < 15 with at least
  MIN_BACKGROUND pixels, plus tiny enclosed specks inside them; encoded as
  exact Y=0, Cb=Cr=128;
- everything else is artwork: Y' = FLOOR + round(Y * (255-FLOOR) / 255), with
  source chroma, so black artwork stays near-black but clearly visible;
- after JPEG encoding, any artwork pixel decoding below SAFE is raised in the
  master and re-encoded (bounded closed loop), keeping a margin over 27 for
  decoder differences. Slot sizes/dimensions/sampling are unchanged.
"""
import argparse
import io
import json
import shutil
from collections import deque
from pathlib import Path
from PIL import Image
from analyze_firmware import EXPECTED_SHA256, parse_resources
from prepare_release_artwork import ROOT, digest, load_rgb, fit_jpeg, sheet
from replace_resource import replace

FRAME_IDS = {6, 7, 8, 9}
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


def prepare(assets, out, sources):
    if out.exists(): raise ValueError('output exists; never overwrite previous assets')
    original = (ROOT/'flash_zb25vq32_read1.bin').read_bytes()
    report = json.loads((assets/'manifest.json').read_text())
    if digest(original) != EXPECTED_SHA256 or report['original_sha256'] != EXPECTED_SHA256:
        raise ValueError('original firmware mismatch')
    _, entries = parse_resources(original); entries = {e['index']: e for e in entries}
    prepared = []
    for item in report['replacements']:
        if digest((assets/item['output']).read_bytes()) != item['sha256']: raise ValueError('asset changed')
        if item['index'] not in FRAME_IDS: continue
        path = sources/Path(item['source']).name
        if digest(path.read_bytes()) != item['source_sha256']: raise ValueError(f'source changed: {path}')
        entry = entries[item['index']]
        with Image.open(io.BytesIO(original[entry['offset']:entry['offset']+entry['size']])) as old:
            result = build_frame(load_rgb(path), old, entry['size'])
        replace(original, item['index'], result[1])
        prepared.append((item, path, *result))
        print(item['index'], json.dumps({k: v for k, v in result[-1].items() if k != 'closed_loop'}),
              result[-1]['closed_loop'][-1])
    shutil.copytree(assets, out)
    masters = out/'frame-mask-masters'; masters.mkdir()
    comparisons = []
    for item, path, image, payload, quality, sampling, bg, target, audit in prepared:
        stem = Path(item['output']).stem
        (out/item['output']).write_bytes(payload)
        with Image.open(io.BytesIO(payload)) as opened: preview = opened.convert('RGB')
        preview.save(out/'resource-previews'/f'{stem}.png')
        Image.frombytes('L', image.size, bytes(255 if b else 0 for b in bg)).save(masters/f'{stem}-transparent.png')
        Image.frombytes('L', image.size, target).save(masters/f'{stem}-Y.png')
        (masters/f'{stem}.ycbcr').write_bytes(image.tobytes())
        item.update(sha256=digest(payload), bytes=len(payload), quality=quality, subsampling=sampling,
                    frame_mask_audit=audit, frame_mask_master=f'frame-mask-masters/{stem}.ycbcr')
        item.pop('frame_curve_audit', None); item.pop('frame_curve_master', None)
        # Simulated composite over mid-gray: photo shows only where Y <= 27.
        dec = decoded_y(payload)
        sim = Image.frombytes('L', image.size, bytes(128 if d <= TRANSPARENT_MAX else d for d in dec))
        comparisons.extend([(load_rgb(path), f'{stem} source'),
                            (sim.convert('RGB'), f'Q{quality} {len(payload):,}/{item["slot_bytes"]:,} B; gray = photo')])
    report.pop('frame_luminance_curve', None)
    report['frame_transparency_mask'] = {
        'stock_rule': 'decoded frame Y <= 27 is transparent (flash 0x40fcc..0x41104)',
        'background': f'4-connected source Y < {CUTOFF} regions >= {MIN_BACKGROUND} px, enclosed specks <= {MAX_SPECK} px; Y=0, Cb=Cr=128',
        'artwork': f'Y = {FLOOR} + round(Y*{255-FLOOR}/255), source chroma',
        'closed_loop': f'artwork decoded below {SAFE} raised and re-encoded, up to {ITERATIONS} passes'}
    report['status'] = 'OFFLINE MASKED FRAME ASSETS (update 05)'
    (out/'manifest.json').write_text(json.dumps(report, indent=2)+'\n')
    sheet(comparisons, out/'frame-mask-comparison.png', 'Frames: source / decoded composite over gray photo', 2, (660, 410))
    (out/'README.md').write_text(__doc__+'\nSee manifest.json frame_mask_audit per frame.\n\n---\nPrevious notes:\n\n'
                                 + (assets/'README.md').read_text())
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--assets', type=Path, default=ROOT/'analysis/release_artwork_04')
    p.add_argument('--sources', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args(); prepare(args.assets, args.output, args.sources)
