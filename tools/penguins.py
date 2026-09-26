"""Random-penguin pictures from a folder (update 37).

Every picture in the penguins folder (natural file-name order) becomes one
print of the menu's *Random penguin*; the firmware picks among exactly these.
Each picture is oriented for the paper roll (landscape pictures are turned 90
degrees so their long side runs along the paper), fitted to 384 dots across and
at most 1024 dots of paper, never cropped, and stored as a grayscale JPEG in the
printer's column layout (prepare_print_penguins.printer_layout) inside the PGPK
v2 pack at flash 0x240000.

Limits: 1..64 pictures (the flash worker's directory), each JPEG <= 128 KiB
(quality is lowered from 90 if needed), and the whole pack must fit the free
flash after the pack address (checked by the integration build).
"""
from pathlib import Path
from frames import natural_key
from prepare_release_artwork import load_rgb, roll_image, encode_jpeg, digest
from prepare_print_penguins import printer_layout, pack, validate

MAX_COUNT = 64                           # flash_penguin_worker directory (update 38; was 16)
MAX_JPEG = 131072                        # prepare_print_penguins.validate
QUALITY = 90
PACK_FLASH = 0x240000                    # persistent_penguins.PACK_FLASH
FLASH_END = 0x400000
EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tif', '.tiff'}


def find(folder):
    folder = Path(folder)
    if not folder.is_dir(): raise ValueError(f'penguins folder not found: {folder}')
    files = sorted((p for p in folder.iterdir() if p.is_file() and not p.name.startswith('.')
                    and p.suffix.lower() in EXTENSIONS), key=natural_key)
    if not files: raise ValueError(f'no pictures in {folder}')
    if len(files) > MAX_COUNT: raise ValueError(f'{len(files)} pictures in {folder}; at most {MAX_COUNT}')
    return files


def encode(path):
    roll, rotated, _ = roll_image(load_rgb(path))
    ready = printer_layout(roll).convert('RGB')
    for quality in range(QUALITY, 29, -1):
        jpeg = encode_jpeg(ready, quality, 2)
        if len(jpeg) <= MAX_JPEG: return jpeg, ready.size, roll.size, rotated, quality
    raise ValueError(f'{path.name}: does not fit {MAX_JPEG} bytes even at quality 30')


def build(folder):
    """(PGPK v2 pack bytes, manifest) for the pictures in `folder`."""
    items = []; records = []
    for number, path in enumerate(find(folder), 1):
        jpeg, size, roll, rotated, quality = encode(path)
        items.append((jpeg, *size))
        records.append({'id': number, 'source': path.name, 'source_sha256': digest(path.read_bytes()),
                        'sha256': digest(jpeg), 'bytes': len(jpeg), 'quality': quality,
                        'rotated_for_roll': rotated, 'logical_roll_dimensions': list(roll),
                        'encoded_dimensions': list(size), 'decode_allocation_bytes': size[0]*384*3//2})
    data = pack(items); validate(data)
    budget = FLASH_END-PACK_FLASH-15                 # the worker's DMA reads round up to 16 bytes
    if len(data) > budget:
        raise ValueError(f'penguin pictures need {len(data):,} bytes of flash, only {budget:,} available: '
                         f'use fewer or simpler pictures (the average is {len(data)//len(items):,} bytes each)')
    manifest = {'version': 2, 'status': 'built from the penguins folder (update 37)',
                'layout': 'encoded JPEG[383-x,y] = logical roll[x,y]; white feed padding aligns width to 32',
                'pack_bytes': len(data), 'pack_sha256': digest(data), 'penguins': records,
                'max_selected_jpeg_plus_decoded_bytes': max(r['bytes']+r['decode_allocation_bytes'] for r in records)}
    return data, manifest
