#!/usr/bin/env python3
"""Convert replacement artwork into the stock MK1-3290-V2 resource formats. Offline only.

    tools/prepare_new_content.py SOURCE_DIR OUTPUT_DIR

Formats, taken from the stock resources and firmware:
- Photo frames 001-010: 1280x720 baseline JPEG. The firmware (RAM 0x02043898) zeroes
  every decoded pixel with Y <= 26 and treats it as transparent. Background becomes
  exact black; artwork brightness is lifted to Y >= 40 + Y*215/255. A closed loop
  re-encodes until no artwork pixel decodes at Y <= 30.
- Screens 011, 033, 048, 049: 320x240 baseline JPEG (4:2:0).
- Menu buttons 032, 034-038: 96x96 24-bit bottom-up BMP with the stock header (54-byte
  header, 2 trailing zero bytes, 3779 ppm). Outside the rounded square: exact
  (140,140,140), the stock background/key colour.
- Menu 033: background 011 + the six buttons at 86x86 in the stock grid.
Needs numpy and Pillow.
"""
import io
import json
import struct
import sys
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

KEY = (140, 140, 140)
THRESHOLD = 26                       # firmware: decoded Y <= 26 -> transparent
SAFE = 30                            # artwork must decode above this
FRAME_SIZE, SCREEN_SIZE = (1280, 720), (320, 240)
MENU_SLOTS = {35: (17, 22), 38: (117, 22), 32: (218, 22),       # measured in stock 033
              36: (17, 131), 34: (117, 131), 37: (218, 131)}
MENU_ICON = 86


def jpeg(im, quality=90):
    out = io.BytesIO()
    im.save(out, 'JPEG', quality=quality, subsampling=2, optimize=False, progressive=False)
    return out.getvalue()


def decode_y(data):
    return np.asarray(Image.open(io.BytesIO(data)).convert('YCbCr'))[:, :, 0].astype(int)


def frame(src, quality):
    rgba = Image.open(src).convert('RGBA')
    rgba = rgba.convert('RGBa').resize(FRAME_SIZE, Image.LANCZOS).convert('RGBA')   # premultiplied resize
    a = np.asarray(rgba)
    art = a[:, :, 3] >= 128
    ycc = np.asarray(rgba.convert('RGB').convert('YCbCr')).astype(float)
    target = ycc.copy()
    target[:, :, 0] = 40 + ycc[:, :, 0] * 215 / 255
    target[~art] = (0, 128, 128)
    lift = np.zeros(art.shape)
    for rounds in range(12):
        t = target.copy()
        t[:, :, 0] = np.minimum(255, t[:, :, 0] + lift)
        im = Image.fromarray(np.clip(np.rint(t), 0, 255).astype(np.uint8), 'YCbCr').convert('RGB')
        data = jpeg(im, quality)
        y = decode_y(data)
        weak = art & (y <= SAFE)
        if not weak.any():
            break
        grow = weak.copy()                                   # raise weak pixels and their neighbours
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            grow |= np.roll(weak, (dy, dx), (0, 1))
        lift[grow & art] += 8
    y = decode_y(data)
    report = dict(bytes=len(data), rounds=rounds + 1,
                  artwork_pixels=int(art.sum()),
                  artwork_decoding_transparent=int((art & (y <= THRESHOLD)).sum()),
                  artwork_min_decoded_y=int(y[art].min()) if art.any() else None,
                  background_decoding_visible=int((~art & (y > THRESHOLD)).sum()))
    return data, y, report


def preview(data, y, path):
    """What the camera shows: transparent pixels as a checkerboard."""
    rgb = np.asarray(Image.open(io.BytesIO(data)).convert('RGB')).copy()
    yy, xx = np.mgrid[:y.shape[0], :y.shape[1]]
    checker = np.where(((yy // 24 + xx // 24) % 2)[..., None] == 0, (200, 120, 220), (120, 200, 230))
    rgb[y <= THRESHOLD] = checker[y <= THRESHOLD]
    Image.fromarray(rgb.astype(np.uint8)).resize((640, 360)).save(path)


def screen(src):
    im = Image.open(src).convert('RGB')
    w, h = im.size                                           # centre-crop to 4:3, then scale
    if w * 3 != h * 4:
        if w * 3 > h * 4:
            nw = h * 4 // 3; im = im.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
        else:
            nh = w * 3 // 4; im = im.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
    return im.resize(SCREEN_SIZE, Image.LANCZOS)


def button_alpha(im):
    """Soft mask of the rounded square: flood the background in from the four corners."""
    a = np.asarray(im.convert('RGB')).astype(float)
    h, w = a.shape[:2]
    corner = np.median(np.stack([a[0, 0], a[0, -1], a[-1, 0], a[-1, -1]]), axis=0)
    dist = np.sqrt(((a - corner) ** 2).sum(axis=2))
    bg = np.zeros((h, w), bool)
    q = deque([(0, 0), (0, w - 1), (h - 1, 0), (h - 1, w - 1)])
    while q:
        y, x = q.popleft()
        if not (0 <= y < h and 0 <= x < w) or bg[y, x] or dist[y, x] > 60:
            continue
        if min(y, h - 1 - y) > 20 and min(x, w - 1 - x) > 20:   # stay near the edge/corners
            continue
        bg[y, x] = True
        q.extend(((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)))
    alpha = np.where(bg, 0.0, 1.0)
    edge = ~bg & np.zeros_like(bg)
    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        edge |= ~bg & np.roll(bg, (dy, dx), (0, 1))
    alpha[edge] = np.clip((dist[edge] - 20) / 80, 0.35, 1.0)    # antialias the outline
    return alpha


def button_bmp(src):
    im = Image.open(src).convert('RGB').resize((96, 96), Image.LANCZOS)
    alpha = button_alpha(im)
    a = np.asarray(im).astype(float)
    out = a * alpha[..., None] + np.array(KEY) * (1 - alpha[..., None])
    out = np.clip(np.rint(out), 0, 255).astype(np.uint8)
    out[alpha < 0.5] = KEY
    inside = (alpha >= 0.5) & np.all(out == KEY, axis=2)
    out[inside] = (141, 140, 140)                                # never the key colour inside
    rows = b''.join(out[y][:, ::-1].tobytes() for y in range(95, -1, -1))  # bottom-up, BGR
    header = struct.pack('<2sIHHI', b'BM', 27704, 0, 0, 54) + struct.pack('<IiiHHIIiiII', 40, 96, 96, 1, 24, 0, 27650, 3779, 3779, 0, 0)
    data = header + rows + b'\0\0'
    assert len(data) == 27704
    return data, Image.fromarray(np.asarray(im)), alpha


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    if out.exists():
        sys.exit(f'refusing to overwrite {out}')
    (out / 'firmware').mkdir(parents=True)
    (out / 'preview').mkdir()
    report = {}

    for f in sorted(src.glob('0*.png')):
        n = int(f.stem)
        if n <= 10:
            data, y, r = frame(f, 88)
            preview(data, y, out / 'preview' / f'{n:03d}_frame_on_camera.png')
        else:
            data = jpeg(screen(f), 92)
            r = dict(bytes=len(data))
        (out / 'firmware' / f'{n:03d}.jpg').write_bytes(data)
        report[f'{n:03d}.jpg'] = r

    buttons = {}
    for f in sorted(src.glob('0*.bmp')):
        n = int(f.stem)
        data, rgb, alpha = button_bmp(f)
        (out / 'firmware' / f'{n:03d}.bmp').write_bytes(data)
        buttons[n] = (rgb, alpha)
        report[f'{n:03d}.bmp'] = dict(bytes=len(data), key_pixels=int((alpha < 0.5).sum()))

    if (out / 'firmware' / '011.jpg').exists() and set(MENU_SLOTS) <= set(buttons):
        menu = Image.open(io.BytesIO((out / 'firmware' / '011.jpg').read_bytes())).convert('RGB')
        menu = screen(src / '011.png')                          # compose from the full-quality source
        for n, (x, y) in MENU_SLOTS.items():
            rgb, alpha = buttons[n]
            icon = Image.open(src / f'{n:03d}.bmp').convert('RGB').resize((MENU_ICON, MENU_ICON), Image.LANCZOS)
            mask = Image.fromarray((alpha * 255).astype(np.uint8)).resize((MENU_ICON, MENU_ICON), Image.LANCZOS)
            menu.paste(icon, (x, y), mask)
        data = jpeg(menu, 92)
        (out / 'firmware' / '033.jpg').write_bytes(data)
        report['033.jpg'] = dict(bytes=len(data), composed_from=['011'] + [f'{n:03d}' for n in MENU_SLOTS])

    # Overview: everything as the firmware will decode it.
    tiles = []
    for f in sorted((out / 'firmware').iterdir()):
        im = Image.open(f).convert('RGB')
        im.thumbnail((300, 170))
        t = Image.new('RGB', (310, 190), (235, 235, 235))
        t.paste(im, ((310 - im.width) // 2, 2))
        ImageDraw.Draw(t).text((5, 175), f'{f.name}  {im.width}x{im.height}  {f.stat().st_size // 1024} KiB', fill=(0, 0, 0))
        tiles.append(t)
    sheet = Image.new('RGB', (310 * 4, 190 * ((len(tiles) + 3) // 4)), 'white')
    for i, t in enumerate(tiles):
        sheet.paste(t, ((i % 4) * 310, (i // 4) * 190))
    sheet.save(out / 'overview.png')
    report['total_bytes'] = sum(v['bytes'] for k, v in report.items() if isinstance(v, dict))
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
