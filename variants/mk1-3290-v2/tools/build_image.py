#!/usr/bin/env python3
"""Build a full 4 MiB flash image with replacement resources. Offline only.

    tools/build_image.py CONTENT_DIR OUTPUT_DIR [--base IMAGE]

CONTENT_DIR holds files named NNN.jpg / NNN.bmp / NNN.wav (resource number).
Firmware facts this relies on (see ANALYSIS.md):
- Resource directory at header[0x18]*512 = 0xd1200, <u32 offset, u32 size>; the
  lookup (RAM 0x02058ed0/0x02058f64) returns base+offset and size with no bound check
  against the resource region, and image loaders allocate `size` bytes.
- Stock flash writers are only the settings sector (end of resources rounded to 4 KiB =
  0x2fd000) and the SD-card DestBin.bin updater, so 0x2fe000..0x400000 is never written.
A replacement that fits its stock slot is written in place (tail left unchanged);
a larger one goes to the free area. Boot header, application and settings are untouched.
"""
import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STOCK_SHA = '33ac2db5716dacf06b60f97c5efbb2b2b77d820fe2b13e58c1eeb4379094fab5'
FREE_START, FREE_END = 0x2fe000, 0x400000
SETTINGS = 0x2fd000
ALIGN = 0x100


def directory(img):
    base = struct.unpack_from('<I', img, 0x18)[0] * 512
    count = struct.unpack_from('<I', img, base)[0] // 8
    return base, [list(struct.unpack_from('<II', img, base + 8 * i)) for i in range(count)]


def check_format(n, data, old):
    kind = 'jpg' if old[:3] == b'\xff\xd8\xff' else 'bmp' if old[:2] == b'BM' else 'wav' if old[:4] == b'RIFF' else None
    if kind == 'jpg':
        from PIL import Image
        import io
        a, b = Image.open(io.BytesIO(old)), Image.open(io.BytesIO(data))
        assert data[:3] == b'\xff\xd8\xff' and b.size == a.size, f'{n}: JPEG size {b.size} != stock {a.size}'
        assert not b.info.get('progressive') and not b.info.get('progression'), f'{n}: progressive JPEG'
    elif kind == 'bmp':
        assert len(data) == len(old) and data[:2] == b'BM', f'{n}: BMP must be {len(old)} bytes'
        assert data[18:30] == old[18:30], f'{n}: BMP width/height/bpp differ from stock'
    elif kind == 'wav':
        assert data[:4] == b'RIFF' and data[20:36] == old[20:36], f'{n}: WAV format differs from stock'
    else:
        sys.exit(f'resource {n} is not a JPEG/BMP/WAV in stock; not supported')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('content', type=Path)
    ap.add_argument('output', type=Path)
    ap.add_argument('--base', type=Path, default=ROOT / 'flash_read1.bin')
    args = ap.parse_args()
    if args.output.exists():
        sys.exit(f'refusing to overwrite {args.output}')
    stock = args.base.read_bytes()
    if len(stock) != 4 << 20:
        sys.exit('base image must be 4 MiB')
    if hashlib.sha256(stock).hexdigest() != STOCK_SHA:
        sys.exit('immutable stock base hash mismatch')
    if any(b != 0xff for b in stock[FREE_START:FREE_END]):
        sys.exit('free area 0x2fe000.. is not erased in the base image')

    img = bytearray(stock)
    base, entries = directory(img)
    cursor, plan = FREE_START, []
    for f in sorted(args.content.iterdir()):
        if f.suffix.lower() not in ('.jpg', '.bmp', '.wav') or not f.stem.isdigit():
            continue
        n = int(f.stem)
        data = f.read_bytes()
        off, size = entries[n]
        old = bytes(stock[base + off:base + off + size])
        check_format(n, data, old)
        if len(data) <= size:
            addr, where = base + off, 'in place'
        else:
            addr = cursor
            if addr + len(data) > FREE_END:
                sys.exit(f'out of free flash at resource {n}')
            cursor = (addr + len(data) + ALIGN - 1) // ALIGN * ALIGN
            where = 'relocated'
        img[addr:addr + len(data)] = data
        entries[n] = [addr - base, len(data)]
        struct.pack_into('<II', img, base + 8 * n, addr - base, len(data))
        plan.append(dict(resource=n, file=f.name, bytes=len(data), stock_bytes=size, flash=f'{addr:#08x}', placement=where,
                         sha256=hashlib.sha256(data).hexdigest()))

    # Verification against the built image.
    nbase, nentries = directory(img)
    assert nbase == base and len(nentries) == len(entries)
    for p in plan:
        o, s = nentries[p['resource']]
        assert hashlib.sha256(img[base + o:base + o + s]).hexdigest() == p['sha256']
    for i, (o, s) in enumerate(nentries):
        if not any(p['resource'] == i for p in plan):
            assert (o, s) == tuple(directory(stock)[1][i]), f'unplanned change to resource {i}'
    assert img[:0xd1200] == stock[:0xd1200], 'boot header/application changed'
    assert img[SETTINGS:FREE_START] == stock[SETTINGS:FREE_START], 'settings sector changed'
    sectors = sorted({a // 4096 * 4096 for a in range(len(img)) if img[a] != stock[a]})

    args.output.mkdir(parents=True)
    (args.output / 'image.bin').write_bytes(img)
    manifest = dict(base_image=str(args.base), base_sha256=hashlib.sha256(stock).hexdigest(),
                    image_sha256=hashlib.sha256(img).hexdigest(), resource_directory=f'{base:#x}',
                    free_area_used=f'{FREE_START:#x}..{cursor:#x} ({cursor - FREE_START} of {FREE_END - FREE_START} bytes)',
                    changed_sectors=[f'{s:#08x}' for s in sectors], resources=plan)
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    for p in plan:
        print(f"{p['resource']:3d} {p['file']:8s} {p['bytes']:7d} B (stock {p['stock_bytes']:6d})  {p['placement']:9s} @ {p['flash']}")
    print(f"changed 4 KiB sectors: {len(sectors)}; free area used up to {cursor:#x}")
    print(f"image {args.output / 'image.bin'} sha256 {manifest['image_sha256']}")


if __name__ == '__main__':
    main()
