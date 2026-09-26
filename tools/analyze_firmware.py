#!/usr/bin/env python3
"""Extract the observed AX3295B resource table; preserve raw entry payloads."""
import argparse
import hashlib
import io
import json
import math
import struct
from collections import Counter
from pathlib import Path
from PIL import Image, ImageDraw

EXPECTED_SHA256 = 'e22557a4497a1199c9ecc3956b18a89ac70af800b674055513f3de91bfb8f224'

def parse_resources(data):
    # Header word 0x18 is a 512-byte sector index in this verified image.
    base = struct.unpack_from('<I', data, 0x18)[0] * 512
    first = struct.unpack_from('<I', data, base)[0]
    assert first % 8 == 0 and 0 < first < 0x10000
    entries = []
    previous = first
    for index in range(first // 8):
        offset, size = struct.unpack_from('<II', data, base + index * 8)
        assert offset == previous, (index, offset, previous)
        assert size > 0 and base + offset + size <= len(data)
        entries.append(dict(index=index, table_offset=base+index*8,
                            offset=base+offset, relative_offset=offset, size=size))
        previous = offset + size
    return base, entries

def analyze(source, destination):
    data = source.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != EXPECTED_SHA256:
        raise ValueError('This extractor currently requires the verified original dump')
    destination.mkdir(parents=True, exist_ok=True)
    resources = destination / 'resources'
    resources.mkdir(exist_ok=True)
    previews = destination / 'previews'
    previews.mkdir(exist_ok=True)
    base, entries = parse_resources(data)
    thumbnails = []
    for entry in entries:
        payload = data[entry['offset']:entry['offset']+entry['size']]
        entry['sha256'] = hashlib.sha256(payload).hexdigest()
        kind = 'bin'
        if payload.startswith(b'\xff\xd8\xff'): kind = 'jpg'
        elif payload.startswith(b'BM'): kind = 'bmp'
        elif payload.startswith(b'RIFF') and payload[8:12] == b'WAVE': kind = 'wav'
        name = f"{entry['index']:03d}_{entry['offset']:06x}.{kind}"
        (resources / name).write_bytes(payload)
        entry['file'] = 'resources/' + name
        entry['kind'] = kind
        entry['prefix_hex'] = payload[:24].hex(' ')
        if kind in ('jpg', 'bmp'):
            try:
                picture = Image.open(io.BytesIO(payload))
                picture.load()
                entry['width'], entry['height'] = picture.size
                entry['mode'] = picture.mode
                png = name.rsplit('.', 1)[0] + '.png'
                picture.convert('RGB').save(previews / png)
                entry['preview'] = 'previews/' + png
                tile = Image.new('RGB', (200, 180), '#eeeeee')
                picture = picture.convert('RGB')
                picture.thumbnail((192, 145))
                tile.paste(picture, ((200-picture.width)//2, 2))
                ImageDraw.Draw(tile).text((4, 150), f"#{entry['index']:02d}  {entry['width']}x{entry['height']}\n0x{entry['offset']:06x}  {entry['size']} B", fill='black')
                thumbnails.append(tile)
            except Exception as error:
                entry['decode_error'] = str(error)
    if thumbnails:
        sheet = Image.new('RGB', (1000, 180 * math.ceil(len(thumbnails)/5)), 'white')
        for i, tile in enumerate(thumbnails): sheet.paste(tile, ((i%5)*200, (i//5)*180))
        sheet.save(destination / 'contact_sheet.jpg', quality=90)
    manifest = dict(source=str(source), sha256=digest, size=len(data),
                    resource_table_offset=base, resource_table_size=entries[0]['relative_offset'],
                    resource_end=entries[-1]['offset']+entries[-1]['size'], entries=entries)
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    # Swap instruction words for GNU's big-endian OR1K disassembler.
    (destination / 'firmware_words_be.bin').write_bytes(b''.join(data[i:i+4][::-1] for i in range(0, base, 4)))
    blocks = []
    for start in range(0, len(data), 4096):
        part = data[start:start+4096]
        entropy = -sum((n/len(part))*math.log2(n/len(part)) for n in Counter(part).values())
        blocks.append(dict(offset=start, entropy=round(entropy, 4), ff=part.count(255), zero=part.count(0)))
    (destination / 'entropy.json').write_text(json.dumps(blocks, indent=2)+'\n')
    print(f"{len(entries)} resources at 0x{base:x}, end 0x{manifest['resource_end']:x}")
    print(Counter(e['kind'] for e in entries))
    print(f"{len(thumbnails)} decoded images; original SHA-256 verified")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    analyze(args.source, args.destination)
