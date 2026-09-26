#!/usr/bin/env python3
"""Replace one image in an offline firmware copy, preserving all slot boundaries.

Experimental: image compatibility and boot integrity still need device validation.
Accepts already-encoded baseline JPEG or same-layout BMP, not arbitrary artwork.
"""
import argparse
import hashlib
import io
import json
import struct
from pathlib import Path
from PIL import Image
from analyze_firmware import EXPECTED_SHA256, parse_resources


def replace(data, index, payload):
    if hashlib.sha256(data).hexdigest() != EXPECTED_SHA256:
        raise ValueError('requires verified original dump')
    _, entries = parse_resources(data)
    if not 0 <= index < len(entries): raise ValueError('resource index out of range')
    entry = entries[index]
    start, size = entry['offset'], entry['size']
    old = data[start:start+size]
    if len(payload) > size: raise ValueError(f'replacement exceeds {size}-byte slot')
    if payload == old: return data, entry
    with Image.open(io.BytesIO(old)) as a, Image.open(io.BytesIO(payload)) as b:
        a.load(); b.load()
        if a.format not in ('JPEG','BMP') or a.format != b.format or a.size != b.size:
            raise ValueError('replacement must match original image format and dimensions')
        if a.mode != b.mode: raise ValueError('replacement image mode must match original')
        if b.format == 'JPEG':
            if b.info.get('progressive') or b.info.get('progression'):
                raise ValueError('progressive JPEG is not supported')
            # Match the original chroma sampling; hardware decoder support is narrow.
            if getattr(a,'layer',None) != getattr(b,'layer',None):
                raise ValueError('JPEG components/subsampling differ from original')
            if payload[-2:] != b'\xff\xd9': raise ValueError('JPEG must end with EOI')
        else:
            # Palette, masks, and header stay byte-identical; replace pixels only.
            header_end = struct.unpack_from('<I', old, 10)[0]
            if len(payload) != size or payload[:header_end] != old[:header_end]:
                raise ValueError('BMP must preserve original header, palette and length')
    result = data[:start] + payload + b'\xff'*(size-len(payload)) + data[start+size:]
    return result, entry


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source', type=Path)
    p.add_argument('index', type=int)
    p.add_argument('replacement', type=Path)
    p.add_argument('output', type=Path)
    args = p.parse_args()
    if args.output.exists(): p.error('output already exists')
    data, entry = replace(args.source.read_bytes(), args.index, args.replacement.read_bytes())
    with args.output.open('xb') as f: f.write(data)
    print(json.dumps(dict(output=str(args.output), slot=entry,
                          sha256=hashlib.sha256(data).hexdigest(),
                          status='OFFLINE ONLY; boot integrity not validated'), indent=2))


if __name__ == '__main__': main()
