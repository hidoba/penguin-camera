"""Photo frames from a folder (update 36).

Every picture in the frames folder becomes one camera frame, in file-name order
(natural sort: 2 before 10). Up/down in the camera then offers exactly these
frames: none -> frame 1 .. frame N -> none (and backwards).

Stock mechanism (20250102 V2.6): the camera state byte 0x020892c8+102 is the
frame index 0..16; the 17-word table at RAM 0x0207e25c maps it to a frame
resource ID (1..17, 640x360 JPEG slots of fixed size in the resource area).
The up/down handlers hard-code the last index 16. So:

- N (1..17) frames go into the N largest stock slots; a table word is hooked
  only where a slot moves, so a stock frame index keeps its slot when it can;
- the five index limits are hooked for N < 17 (down: last -> none; up: none ->
  last; the second up/down handler pair at 0x11a74 / 0x11c3c wraps at N-1).

Picture rules: 640x360 or the same 16:9 shape (scaled, no crop). Transparent
areas (the photo shows through) come from the alpha channel when the picture
has one (alpha < 128); otherwise from large pure-black regions (source Y < 15),
exactly like the original artwork. See prepare_frame_mask.py for the encoding
(artwork lifted to Y >= 40, closed-loop check that artwork never decodes as
transparent, JPEG fitted into the slot).
"""
import io
import re
import struct
from pathlib import Path
from PIL import Image, ImageOps, ImageCms
from analyze_firmware import parse_resources
from ui_trace_patch import BIAS

TABLE = 0x0207e25c                      # 17 u32 resource IDs
COUNT = 17                              # stock frame slots (resources 1..17)
SIZE = (640, 360)
EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tif', '.tiff'}
MAX_HOOKS = 64                          # loader rehearsal mirror (persistent_loader)

# (flash offset, stock word, encoder(n) -> replacement word, label)
LIMITS = (
    (0xc06c, 0xbc44000f, lambda n: 0xbc640000 | (n-1), 'down: last frame -> none (l.sfgeui r4,N-1)'),
    (0xc2cc, 0x9c600010, lambda n: 0x9c600000 | (n-1), 'up: none -> last frame index'),
    (0xc2dc, 0x84630040, lambda n: 0x84630000 | 4*(n-1), 'up: none -> last frame table entry'),
    (0x11a88, 0x9c600010, lambda n: 0x9c600000 | (n-1), 'frame handler 2: previous wraps to last'),
    (0x11c48, 0xbc430010, lambda n: 0xbc430000 | (n-1), 'frame handler 2: next wraps after last'),
)


def natural_key(path):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', path.name)]


def find(folder):
    """Frame pictures in `folder`, in camera order. Raises if none or > 17."""
    folder = Path(folder)
    if not folder.is_dir(): raise ValueError(f'frames folder not found: {folder}')
    files = sorted((p for p in folder.iterdir() if p.is_file() and not p.name.startswith('.')
                    and p.suffix.lower() in EXTENSIONS), key=natural_key)
    if not files: raise ValueError(f'no frame pictures in {folder}')
    if len(files) > COUNT: raise ValueError(f'{len(files)} frames in {folder}; the camera has {COUNT} frame slots')
    return files


def stock_table(original):
    return list(struct.unpack_from(f'<{COUNT}I', original, TABLE-BIAS))


def slot_sizes(original):
    _, entries = parse_resources(original)
    return {e['index']: e['size'] for e in entries if 1 <= e['index'] <= COUNT}


def assign(original, n):
    """Resource ID for frame index 0..n-1: the n largest slots; a slot stays at
    its stock index when possible (fewer hooks), the rest fill the gaps."""
    if not 1 <= n <= COUNT: raise ValueError(f'frame count must be 1..{COUNT}')
    sizes = slot_sizes(original); table = stock_table(original)
    if sorted(table) != list(range(1, COUNT+1)): raise ValueError('unexpected stock frame table')
    chosen = sorted(sizes, key=lambda i: (-sizes[i], i))[:n]
    slots = [table[k] if table[k] in chosen else None for k in range(n)]
    free = [i for i in chosen if i not in slots]
    return [s if s is not None else free.pop(0) for s in slots]


def hooks(original, slots):
    """RAM word hooks: index limits (n < 17) and moved table entries."""
    n = len(slots); out = []
    def hook(address, old, new, label):
        if struct.unpack_from('<I', original, address-BIAS)[0] != old: raise ValueError(f'unexpected stock word at {address:#x}')
        out.append({'address': address, 'original': struct.pack('<I', old).hex(),
                    'replacement': struct.pack('<I', new).hex(), 'label': label})
    if n < COUNT:
        for off, old, new, label in LIMITS: hook(BIAS+off, old, new(n), f'frames: {label} (N={n})')
    for k, (old, new) in enumerate(zip(stock_table(original), slots)):
        if old != new: hook(TABLE+4*k, old, new, f'frames: index {k} -> resource {new}')
    return out


def load(path):
    """RGB picture at 640x360 and its transparency mask (None: use the black rule)."""
    with Image.open(path) as opened:
        if getattr(opened, 'n_frames', 1) != 1: raise ValueError(f'animated/multipage frame: {path}')
        im = ImageOps.exif_transpose(opened); im.load()
    alpha = im.getchannel('A') if 'A' in im.getbands() else (
        im.convert('RGBA').getchannel('A') if im.mode == 'P' and 'transparency' in im.info else None)
    profile = im.info.get('icc_profile')
    rgb = im.convert('RGB')
    if profile:
        rgb = ImageCms.profileToProfile(rgb, ImageCms.ImageCmsProfile(io.BytesIO(profile)),
                                        ImageCms.createProfile('sRGB'), outputMode='RGB')
    if rgb.size != SIZE:
        w, h = rgb.size
        if abs(w*SIZE[1]-h*SIZE[0]) > 0.02*h*SIZE[0]:
            raise ValueError(f'{path.name}: {w}x{h} is not 16:9 (frames are {SIZE[0]}x{SIZE[1]})')
        rgb = rgb.resize(SIZE, Image.Resampling.LANCZOS)
        if alpha is not None: alpha = alpha.resize(SIZE, Image.Resampling.LANCZOS)
    if alpha is None: return rgb, None
    mask = [a < 128 for a in alpha.tobytes()]
    if not any(mask): return rgb, None          # opaque alpha: black rule
    return rgb, mask


def prepare(original, folder):
    """[(frame number, source path, resource ID, JPEG payload, audit)] for the folder."""
    from prepare_frame_mask import build_frame
    files = find(folder); slots = assign(original, len(files))
    _, entries = parse_resources(original); entries = {e['index']: e for e in entries}
    out = []
    for number, (path, rid) in enumerate(zip(files, slots), 1):
        entry = entries[rid]
        rgb, mask = load(path)
        with Image.open(io.BytesIO(original[entry['offset']:entry['offset']+entry['size']])) as old:
            if old.size != SIZE: raise ValueError(f'stock frame slot {rid} is not {SIZE}')
            _, payload, quality, _, _, _, audit = build_frame(rgb, old, entry['size'], mask)
        audit.update(quality=quality, slot_bytes=entry['size'], transparency='alpha' if mask else 'black regions')
        out.append((number, path, rid, payload, audit))
    return out
