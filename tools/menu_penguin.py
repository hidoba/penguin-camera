"""Baby penguin peeking into the native menu, top-right corner (update 31).

Source: analysis/menu_penguin_01/source.webp (user image); processed once into
analysis/menu_penguin_01/penguin_96x167_16gray.png (crop 923,0..1254,576, Lanczos
to 96x167, 16 gray levels v*17). Stored as 4-bit luma (low nibble = even x), split
by rows over three free payload blocks after the sliced Floyd / Atkinson / Stucki
kernels; drawn into the menu's linear 320x240 Y plane after the text renderer.
"""
from live_preview_patch import Code

from project_paths import MENU_PENGUIN as IMAGE
W, H = 96, 167
X0, Y0 = 320-W, 0
BYTES_PER_ROW = W//2
CHUNKS = ((0x1540, 0x2100), (0x2540, 0x3100), (0x3640, 0x4200))   # free tails (code ends 0x152c/0x253c/0x3634)
CODE = 0x2bb00                                                     # after the menu renderer (ends 0x2ba74)
CODE_END = 0x2bd00                                                 # portrait renderer


def levels():
    from PIL import Image
    im = Image.open(IMAGE).convert('L')
    if im.size != (W, H): raise ValueError('unexpected penguin size')
    return [v//17 for v in im.tobytes()]


def rows_per_chunk():
    return [(end-start)//BYTES_PER_ROW for start, end in CHUNKS]


def chunks():
    """{offset: bytes} for the packed rows; returns the per-chunk row counts too."""
    lv = levels(); out = {}; row = 0; counts = []
    for (start, end), cap in zip(CHUNKS, rows_per_chunk()):
        n = min(cap, H-row); counts.append(n)
        data = bytes(lv[(row+r)*W+x] | lv[(row+r)*W+x+1] << 4 for r in range(n) for x in range(0, W, 2))
        out[start] = data; row += n
    if row != H: raise ValueError('penguin does not fit the free blocks')
    return out, counts


def reference(y_plane):
    out = bytearray(y_plane); lv = levels()
    for y in range(H):
        for x in range(W): out[(Y0+y)*320+X0+x] = lv[y*W+x]*17
    return bytes(out)


def blit(base):
    """r3 = 320x240 linear Y plane (validated by the caller). Preserves every register."""
    _, counts = chunks()
    a = Code(); imm = a.immediate
    saved = tuple(range(12, 21))
    imm(0x27, 1, 1, -len(saved)*4)
    for i, r in enumerate(saved): a.store(r, 1, i*4)
    a.const(12, Y0*320+X0); a.alu(12, 3, 12)                 # destination row start
    for k, ((start, _), n) in enumerate(zip(CHUNKS, counts)):
        a.const(13, base+start); a.const(14, n)               # source, rows left in chunk
        a.label(f'row{k}'); imm(0x27, 15, 0, 0)               # byte in row
        a.label(f'byte{k}')
        a.alu(16, 13, 15); imm(0x23, 16, 16, 0)
        imm(0x29, 17, 16, 15); imm(0x27, 18, 0, 17); a.alu(17, 17, 18, 0x306)
        imm(0x2e, 16, 16, 0x44); a.alu(16, 16, 18, 0x306)
        imm(0x2e, 19, 15, 1); a.alu(19, 12, 19)
        a.store(17, 19, 0, op=0x36); a.store(16, 19, 1, op=0x36)
        imm(0x27, 15, 15, 1); imm(0x2f, 1, 15, BYTES_PER_ROW); a.branch(4, f'byte{k}')
        imm(0x27, 13, 13, BYTES_PER_ROW); imm(0x27, 12, 12, 320)
        imm(0x27, 14, 14, -1); imm(0x2f, 1, 14, 0); a.branch(4, f'row{k}')
    for i, r in enumerate(saved): imm(0x21, r, 1, i*4)
    imm(0x27, 1, 1, len(saved)*4); a.emit(0x44004800)
    code = a.finish()
    if CODE+len(code) > CODE_END: raise ValueError('menu penguin code overflow')
    return code
