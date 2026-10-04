#!/usr/bin/env python3
"""Export every resource of the MK1-3290-V2 (AX3291A) firmware dump. Offline only.

Resource directory: header word 0x18 * 512 = 0xd1200; <II> (relative offset, size)
entries, contiguous, 85 entries. Raw payloads are kept byte-exact in raw/ so they can
be compared against replacements; everything else is a viewable/editable rendering.

    python3 tools/extract_resources.py OUTPUT_DIR
"""
import hashlib
import io
import json
import math
import shutil
import struct
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
DUMP_SHA = '33ac2db5716dacf06b60f97c5efbb2b2b77d820fe2b13e58c1eeb4379094fab5'


def parse(data):
    base = struct.unpack_from('<I', data, 0x18)[0] * 512
    first = struct.unpack_from('<I', data, base)[0]
    out, prev = [], first
    for i in range(first // 8):
        off, size = struct.unpack_from('<II', data, base + i * 8)
        assert off == prev, (i, off, prev)
        out.append((i, base + off, size))
        prev = off + size
    return base, out


def yuv420(p, w, h):
    """w*h Y plane followed by (w/2*h/2) interleaved VU pairs (as on the AX3295B)."""
    im = Image.new('YCbCr', (w, h))
    c = p[w * h:]
    im.putdata([(p[y * w + x], c[(y // 2 * (w // 2) + x // 2) * 2 + 1], c[(y // 2 * (w // 2) + x // 2) * 2])
                for y in range(h) for x in range(w)])
    return im.convert('RGB')


def bits_glyph(p, w, h):
    stride = (w + 7) // 8
    im = Image.new('L', (w, h), 255)
    for y in range(h):
        for x in range(w):
            if p[y * stride + x // 8] & (0x80 >> (x % 8)):
                im.putpixel((x, y), 0)
    return im


def sheet(images, cols, scale=1, pad=4, label=None):
    if not images:
        return None
    cw = max(i.width for i in images) * scale + pad
    ch = max(i.height for i in images) * scale + pad + (12 if label else 0)
    rows = math.ceil(len(images) / cols)
    out = Image.new('RGB', (cw * cols, ch * rows), (230, 230, 230))
    draw = ImageDraw.Draw(out)
    for n, im in enumerate(images):
        x, y = (n % cols) * cw, (n // cols) * ch
        out.paste(im.convert('RGB').resize((im.width * scale, im.height * scale), Image.NEAREST), (x + pad // 2, y + pad // 2))
        if label:
            draw.text((x + 2, y + ch - 12), label(n), fill=(0, 0, 0))
    return out


def font50(p):
    """875 proportional 1-bpp glyphs, 32 px tall: <I count><I ?> then <H width><H height><I offset>."""
    n = struct.unpack_from('<I', p, 0)[0]
    glyphs = []
    for i in range(n):
        w, h, off = struct.unpack_from('<HHI', p, 8 + 8 * i)
        glyphs.append(bits_glyph(p[off:], max(w, 1), h) if w else Image.new('L', (1, h), 255))
    return glyphs


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    out = Path(sys.argv[1])
    if out.exists():
        sys.exit(f'refusing to overwrite {out}')
    data = (ROOT / 'flash_read1.bin').read_bytes()
    if hashlib.sha256(data).hexdigest() != DUMP_SHA:
        sys.exit('flash_read1.bin hash mismatch')
    base, entries = parse(data)
    for d in ('raw', 'images', 'sounds', 'fonts', 'text', 'game-data'):
        (out / d).mkdir(parents=True)
    res = {i: data[o:o + s] for i, o, s in entries}
    manifest, thumbs = [], []

    def note(i, kind, desc, files, **extra):
        o = next(e[1] for e in entries if e[0] == i)
        manifest.append(dict(index=i, flash_offset=f'{o:#08x}', size=len(res[i]), kind=kind,
                             description=desc, files=files, sha256=hashlib.sha256(res[i]).hexdigest(), **extra))

    for i, off, size in entries:
        p = res[i]
        ext = 'jpg' if p[:3] == b'\xff\xd8\xff' else 'bmp' if p[:2] == b'BM' else 'wav' if p[:4] == b'RIFF' else 'bin'
        (out / 'raw' / f'{i:03d}.{ext}').write_bytes(p)
        if ext in ('jpg', 'bmp'):
            im = Image.open(io.BytesIO(p))
            im.load()
            name = f'images/{i:03d}_{im.width}x{im.height}.png'
            im.convert('RGB').save(out / name)
            shutil.copyfile(out / 'raw' / f'{i:03d}.{ext}', out / f'images/{i:03d}_{im.width}x{im.height}.{ext}')
            extra = dict(width=im.width, height=im.height)
            if ext == 'bmp':
                extra['bmp_bits'] = struct.unpack_from('<H', p, 28)[0]
            note(i, ext, f'{ext.upper()} {im.width}x{im.height}', [name, name[:-3] + ext], **extra)
            thumbs.append((i, im.convert('RGB')))
        elif ext == 'wav':
            fmt, ch, rate, _, _, bits = struct.unpack_from('<HHIIHH', p, 20)
            name = f'sounds/{i:03d}_{rate}Hz_{"stereo" if ch == 2 else "mono"}.wav'
            (out / name).write_bytes(p)
            note(i, 'wav', f'PCM {bits}-bit {ch}ch {rate} Hz, {len(p) / (rate * ch * bits / 8):.2f} s', [name],
                 rate=rate, channels=ch, bits=bits)

    # Raw YUV420 sprites (Y plane + interleaved VU), identified by size and content.
    for i, w, h, tiles, desc in ((29, 14, 14, 1, 'game sprite 14x14 YUV420'),
                                 (26, 30, 30, 6, 'game sprites, 6 tiles 30x30 YUV420')):
        step = w * h * 3 // 2
        ims = [yuv420(res[i][t * step:(t + 1) * step], w, h) for t in range(tiles)]
        name = f'images/{i:03d}_yuv420_{w}x{h}x{tiles}.png'
        sheet(ims, tiles, scale=4).save(out / name)
        note(i, 'yuv420', desc, [name], width=w, height=h, tiles=tiles)
        thumbs.append((i, sheet(ims, tiles)))

    # Palette-indexed game sprites/maps (1 byte per pixel, small indices; palette unknown).
    gray = lambda p, w, mul: Image.frombytes('L', (w, len(p) // w), bytes(min(255, v * mul) for v in p[:len(p) // w * w]))
    for i, w, desc in ((24, 30, 'game level maps, 10 x (30x11) cells'),
                       (25, 14, 'indexed game sprites, 6 x 14x14, values 0..3'),
                       (27, 8, 'indexed game sprites/tiles, 8 px wide, values 0..6'),
                       (23, 24, 'small indexed graphic, 0 = transparent (layout uncertain)'),
                       (21, 16, 'unknown 8-bit graphic/tile data, 16 px wide guess (layout uncertain)')):
        name = f'game-data/{i:03d}_preview_w{w}.png'
        im = gray(res[i], w, 40 if max(res[i]) < 8 else 1)
        im.resize((im.width * 4, im.height * 4), Image.NEAREST).save(out / name)
        note(i, 'game-data', desc, [name, f'raw/{i:03d}.bin'])

    # Palette: 256 x <H rgb565><H flags>.
    pal = res[47]
    sw = Image.new('RGB', (16 * 24, 16 * 24))
    dr = ImageDraw.Draw(sw)
    for k in range(256):
        v = struct.unpack_from('<H', pal, 4 * k)[0]
        rgb = ((v >> 11) * 255 // 31, ((v >> 5) & 63) * 255 // 63, (v & 31) * 255 // 31)
        dr.rectangle([(k % 16) * 24, (k // 16) * 24, (k % 16) * 24 + 23, (k // 16) * 24 + 23], fill=rgb)
    sw.save(out / 'images/047_palette_rgb565.png')
    note(47, 'palette', '256-entry RGB565 palette (+16-bit flags), probably on-screen display colours', ['images/047_palette_rgb565.png'])

    # Fonts.
    p = res[39]
    n = struct.unpack_from('<I', p, 0)[0]
    ents = [struct.unpack_from('<II', p, 4 + 8 * k) for k in range(n)]
    g39, codes = [], []
    for k, (code, o) in enumerate(ents):
        nxt = ents[k + 1][1] if k + 1 < n else len(p) - 4
        size = nxt - o
        w, h = {8: (8, 8), 16: (8, 16), 32: (16, 16)}.get(size, (8, size))
        g39.append(bits_glyph(p[4 + o:], w, h))
        codes.append(code)
    sheet(g39, 16, 3, label=lambda k: f'{codes[k]:#x}').save(out / 'fonts/039_font_small_1bpp.png')
    note(39, 'font', f'{n}-glyph 1-bpp font (ASCII 8 px wide + 16x16 CJK), <I code><I offset>', ['fonts/039_font_small_1bpp.png'])

    p = res[46]
    colours = []
    for k in range(256):
        v, a = struct.unpack_from('<HH', pal, 4 * k)
        colours.append(((v >> 11) * 255 // 31, ((v >> 5) & 63) * 255 // 63, (v & 31) * 255 // 31, 255 if a else 0))
    g46 = []
    for k in range(struct.unpack_from('<I', p, 8)[0] // 12):
        w, h, o = struct.unpack_from('<III', p, 12 * k)
        im = Image.new('RGBA', (w, h))
        im.putdata([colours[v] for v in p[o:o + w * h]])
        g46.append(im)
        im.save(out / f'images/046_osd_icon_{k:02d}_{w}x{h}.png')
    sheet([Image.alpha_composite(Image.new('RGBA', i.size, (120, 120, 120, 255)), i) for i in g46], 12, 3,
          label=lambda k: f'{k} {g46[k].width}x{g46[k].height}').save(out / 'images/046_osd_icons_sheet.png')
    note(46, 'osd-icons', f'{len(g46)} on-screen icons, 8-bit indices into palette 47, <I w><I h><I offset>',
         ['images/046_osd_icons_sheet.png', 'images/046_osd_icon_NN_WxH.png'])

    g50 = font50(res[50])
    sheet(g50, 32, 1, label=str).save(out / 'fonts/050_font_32px_1bpp.png')
    note(50, 'font', f'{len(g50)}-glyph proportional 1-bpp font, 32 px tall (used by the string table 51)', ['fonts/050_font_32px_1bpp.png'])

    # Multilingual strings (resource 51): 'MX', 18 language blocks of 187 strings of font-50 glyph indices.
    p = res[51]
    langs = p[3]
    files = []
    for L in range(langs):
        _id, o = struct.unpack_from('<II', p, 8 + 8 * L)
        blk = p[o:]
        first = struct.unpack_from('<HHHH', blk, 8)[3]
        count = (first - 8) // 8
        lines = []
        for s in range(count):
            w, h, nchar, so = struct.unpack_from('<HHHH', blk, 8 + 8 * s)
            idx = struct.unpack_from(f'<{nchar}H', blk, so)
            img = Image.new('L', (max(w, 1), 32), 255)
            x = 0
            for g in idx:
                if g < len(g50):
                    img.paste(g50[g], (x, 0))
                    x += g50[g].width
            lines.append(img)
        name = f'text/051_language_{L:02d}.png'
        sheet(lines, 4, 1, label=lambda k: f'#{k}').save(out / name)
        files.append(name)
    note(51, 'strings', f'"MX" string table: {langs} languages x {count} menu strings, as font-50 glyph indices', files)

    for i, desc in ((82, 'version string'),):
        note(i, 'text', f'{desc}: {res[i].decode(errors="replace")}', [f'raw/{i:03d}.bin'])
    note(0, 'empty', 'zero-length entry', [])

    # Contact sheet of all pictures.
    tiles = []
    for i, im in thumbs:
        t = Image.new('RGB', (200, 170), (238, 238, 238))
        im = im.copy()
        im.thumbnail((192, 140))
        t.paste(im, ((200 - im.width) // 2, 2))
        ImageDraw.Draw(t).text((4, 150), f'#{i:02d} {next(m["description"] for m in manifest if m["index"] == i)[:30]}', fill=(0, 0, 0))
        tiles.append(t)
    cs = Image.new('RGB', (1000, 170 * math.ceil(len(tiles) / 5)), 'white')
    for n, t in enumerate(tiles):
        cs.paste(t, ((n % 5) * 200, (n // 5) * 170))
    cs.save(out / 'contact_sheet.png')

    manifest.sort(key=lambda m: m['index'])
    (out / 'manifest.json').write_text(json.dumps(dict(source='flash_read1.bin', sha256=DUMP_SHA,
                                                       directory=f'{base:#x}', entries=manifest), indent=2) + '\n')
    print(f'{len(manifest)} of {len(entries)} resources described in {out}')


if __name__ == '__main__':
    main()
