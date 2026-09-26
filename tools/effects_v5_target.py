"""Native OpenRISC kernels for update 05 (Bayer 8x8 + edges, Halftone).

ABI shared with the resident Magic/1D kernels: r3=Y (in place), r4=width,
r5=height, r6=scratch, r11=0 success / 8 reject, all other registers preserved.
No calls, allocation, cache maintenance or device access. Scratch must be one
of the payload's two known private scratch areas (preview: width <= 320).
Bit-exact against the host oracles in effects_v5.py.
"""
import struct
from live_preview_patch import Code, TABLES
from responsive_effects_patch import PREVIEW_SCRATCH
from persistent_effects_patch import PRINT_SCRATCH
from effects_v5 import EDGE_THRESHOLD, halftone_thresholds

# Formerly the retained 672-row print scratch; no longer referenced by any
# resident code (print uses PRINT_SCRATCH). Checked by the integration build.
EDGE = 0x9000          # update 09: this slot now holds Bayer 16x16
BAYER16 = 0x9000
HALFTONE = 0x9800
HALFTONE_TABLE = 0xa000
BAYER16_TABLE = 0xa0c0
# Update 35: HALFTONE 5X5 (mode 9, in place of the hidden Bayer 16x16) in the free
# tail of the print-kernel slot 0x5000 (kernel 0x5000..0x53e0).
HALFTONE5 = 0x5400
HALFTONE5_TABLE = 0x5c00
HALFTONE5_END = 0x5c80
HALFTONE5_TAPS = (1, 7, 16)     # sigma 0.83 = 5/6
REGION = (0x9000, 0xa1c0)       # includes HALFTONE_SLICED 0x9200..0x9708
MAX_CODE = 0x800
MIN_WIDTH = 8

SAVED = tuple(r for r in range(2, 32) if r not in (9, 11))  # r9 is our return


def _prologue(a, base, max_dimension, allocation_size, spill=0):
    """Stack: spill slots at sp+0.., saved registers above them."""
    imm = a.immediate
    frame = spill+len(SAVED)*4
    a.spill = spill
    imm(0x27, 1, 1, -frame)
    for i, r in enumerate(SAVED): a.store(r, 1, spill+i*4)
    imm(0x2f, 4, 4, MIN_WIDTH); a.branch(4, 'reject')
    imm(0x2f, 2, 4, max_dimension); a.branch(4, 'reject')
    imm(0x2f, 4, 5, 1); a.branch(4, 'reject')
    imm(0x2f, 2, 5, max_dimension); a.branch(4, 'reject')
    # Image: inside heap RAM, below the stack, disjoint from our allocation.
    a.const(12, 0x02090000); a.compare(3, 12, 4); a.branch(4, 'reject')
    a.alu(13, 4, 5, 0x306); a.alu(13, 3, 13)
    a.const(12, 0x02200000); a.compare(13, 12, 2); a.branch(4, 'reject')
    a.compare(13, 1, 2); a.branch(4, 'reject')
    a.const(12, base+allocation_size); a.compare(3, 12, 3); a.branch(4, 'disjoint')
    a.const(12, base); a.compare(13, 12, 2); a.branch(4, 'reject')
    a.label('disjoint')
    # Scratch: only the exact private areas, each sized 12*width.
    a.const(12, base+PRINT_SCRATCH); a.compare(6, 12, 0); a.branch(4, 'scratch_ok')
    a.const(12, base+PREVIEW_SCRATCH); a.compare(6, 12, 1); a.branch(4, 'reject')
    imm(0x2f, 2, 4, 320); a.branch(4, 'reject')
    a.label('scratch_ok')
    return frame


def _epilogue(a, frame):
    imm = a.immediate
    imm(0x27, 11, 0, 0); a.branch(0, 'restore')
    a.label('reject'); imm(0x27, 11, 0, 8)
    a.label('restore')
    for i, r in enumerate(SAVED): imm(0x21, r, 1, getattr(a, 'spill', 0)+i*4)
    imm(0x27, 1, 1, frame); a.emit(0x44004800)
    code = a.finish()
    if len(code) > MAX_CODE: raise ValueError('kernel slot overflow')
    return code


def _abs(a, r, label):
    a.immediate(0x2f, 11, r, 0); a.branch(4, label)
    a.alu(r, 0, r, 2)
    a.label(label)


def _mag_row(a, tag, dst, yy):
    """Magnitude row yy (register) into u16 row dst; zero unless interior.
    Uses r2,r8,r12..r19,r26..r29,r31. Needs r3=image, r4=w, r25=h-1."""
    imm = a.immediate
    imm(0x2f, 4, yy, 1); a.branch(4, f'{tag}_zero')
    a.compare(yy, 25, 3); a.branch(4, f'{tag}_zero')
    imm(0x27, 12, yy, -1); a.alu(12, 12, 4, 0x306); a.alu(15, 3, 12)
    a.alu(16, 15, 4); a.alu(17, 16, 4)
    a.store(0, dst, 0, op=0x37)
    a.alu(12, 4, 4); a.alu(12, dst, 12); a.store(0, 12, -2, op=0x37)
    imm(0x27, 15, 15, 1); imm(0x27, 16, 16, 1); imm(0x27, 17, 17, 1)
    imm(0x27, 29, dst, 2); imm(0x27, 8, 4, -2)
    a.label(f'{tag}_mag')
    imm(0x23, 12, 15, -1); imm(0x23, 13, 15, 1)
    imm(0x23, 14, 16, -1); imm(0x23, 18, 16, 1)
    imm(0x23, 19, 17, -1); imm(0x23, 26, 17, 1)
    imm(0x23, 27, 15, 0); imm(0x23, 28, 17, 0)
    a.alu(2, 13, 12, 2); a.alu(31, 18, 14, 2); a.alu(31, 31, 31); a.alu(2, 2, 31)
    a.alu(31, 26, 19, 2); a.alu(2, 2, 31)
    a.alu(31, 19, 12, 2); a.alu(28, 28, 27, 2); a.alu(28, 28, 28); a.alu(31, 31, 28)
    a.alu(28, 26, 13, 2); a.alu(31, 31, 28)
    _abs(a, 2, f'{tag}_gx'); _abs(a, 31, f'{tag}_gy')
    a.alu(12, 2, 31); imm(0x2e, 12, 12, 0x42); imm(0x2e, 12, 12, 1)
    a.compare(2, 31, 11); a.branch(3, f'{tag}_vert'); imm(0x2a, 12, 12, 1)
    a.label(f'{tag}_vert'); a.store(12, 29, 0, op=0x37)
    imm(0x27, 29, 29, 2); imm(0x27, 15, 15, 1); imm(0x27, 16, 16, 1); imm(0x27, 17, 17, 1)
    imm(0x27, 8, 8, -1); imm(0x2f, 1, 8, 0); a.branch(4, f'{tag}_mag')
    a.branch(0, f'{tag}_done')
    a.label(f'{tag}_zero'); imm(0x2a, 12, dst, 0); imm(0x2a, 14, 4, 0)
    a.label(f'{tag}_zloop'); a.store(0, 12, op=0x37); imm(0x27, 12, 12, 2)
    imm(0x27, 14, 14, -1); imm(0x2f, 1, 14, 0); a.branch(4, f'{tag}_zloop')
    a.label(f'{tag}_done')


def edge_kernel(base, max_dimension=1024, allocation_size=0x30400, threshold=EDGE_THRESHOLD):
    """Bayer 8x8 with black Sobel contours dilated 3x3.

    Scratch: three u16 magnitude rows (y, y+1, y+2) then three edge-byte rows
    (y-1, y, y+1) of stride w+2 with zero padding: 9w+6 <= 12w. Magnitude
    row y+2 only needs input rows >= y+1, so rows are overwritten in place.
    """
    a = Code(); imm = a.immediate
    frame = _prologue(a, base, max_dimension, allocation_size, spill=8)
    # r20/r21/r22 = M(y), M(y+1), M(y+2); r23/r24 = E(y-1), E(y); E(y+1) in sp+0
    imm(0x2a, 20, 6, 0); a.alu(12, 4, 4); a.alu(21, 20, 12); a.alu(22, 21, 12)
    a.alu(30, 22, 12)                                   # E base
    imm(0x2a, 12, 6, 0); imm(0x27, 14, 0, 9); a.alu(14, 4, 14, 0x306); imm(0x27, 14, 14, 6)
    a.alu(14, 12, 14)
    a.label('zero'); a.store(0, 12, op=0x36); imm(0x27, 12, 12, 1)
    a.compare(12, 14, 4); a.branch(4, 'zero')
    imm(0x27, 12, 4, 2)
    imm(0x2a, 23, 30, 0); a.alu(24, 23, 12); a.alu(12, 24, 12); a.store(12, 1, 0)
    imm(0x27, 25, 5, -1)
    imm(0x27, 7, 0, 1); _mag_row(a, 'init', 21, 7)      # M(1); M(0) stays zero
    imm(0x27, 7, 0, 0); imm(0x2a, 30, 3, 0)             # y, row y pointer
    a.label('row')
    imm(0x27, 12, 7, 2); _mag_row(a, 'loop', 22, 12)
    # ---- E(y+1) into Ec using M(y)=r20, M(y+1)=r21, M(y+2)=r22 ----
    imm(0x21, 28, 1, 0)                       # Ec
    imm(0x27, 8, 0, 0); imm(0x27, 29, 0, 0)
    a.label('edge')
    a.alu(16, 21, 29); imm(0x25, 18, 16, 0); imm(0x2e, 19, 18, 0x41)
    imm(0x27, 14, 0, 0)
    imm(0x27, 15, 0, threshold); a.compare(19, 15, 5); a.branch(4, 'edge_store')
    imm(0x29, 18, 18, 1); imm(0x2f, 0, 18, 0); a.branch(4, 'edge_y')
    imm(0x25, 26, 16, -2); imm(0x25, 27, 16, 2); a.branch(0, 'edge_nms')
    a.label('edge_y')
    a.alu(17, 20, 29); imm(0x25, 26, 17, 0); a.alu(17, 22, 29); imm(0x25, 27, 17, 0)
    a.label('edge_nms')
    imm(0x2e, 26, 26, 0x41); imm(0x2e, 27, 27, 0x41)
    a.compare(19, 26, 4); a.branch(4, 'edge_store')
    a.compare(19, 27, 5); a.branch(4, 'edge_store')
    imm(0x27, 14, 0, 1)
    a.label('edge_store'); a.alu(12, 28, 8); a.store(14, 12, 1, op=0x36)
    imm(0x27, 8, 8, 1); imm(0x27, 29, 29, 2); a.compare(8, 4, 4); a.branch(4, 'edge')
    # ---- output row y: dilated edge -> black, else Bayer ----
    imm(0x29, 31, 7, 7); imm(0x2e, 31, 31, 3); a.const(12, base+TABLES); a.alu(31, 12, 31)
    imm(0x27, 26, 0, 0)                                   # left column OR
    imm(0x23, 12, 23, 1); imm(0x23, 13, 24, 1); a.alu(27, 12, 13, 4)
    imm(0x23, 12, 28, 1); a.alu(27, 27, 12, 4)            # middle column OR
    imm(0x27, 8, 0, 0)
    a.label('pixel')
    a.alu(12, 23, 8); imm(0x23, 13, 12, 2); a.alu(12, 24, 8); imm(0x23, 12, 12, 2)
    a.alu(13, 13, 12, 4); a.alu(12, 28, 8); imm(0x23, 12, 12, 2); a.alu(29, 13, 12, 4)
    a.alu(12, 26, 27, 4); a.alu(12, 12, 29, 4)
    a.alu(15, 30, 8); imm(0x27, 14, 0, 0)
    imm(0x2f, 1, 12, 0); a.branch(4, 'store')
    imm(0x23, 12, 15, 0)
    imm(0x29, 13, 8, 7); a.alu(13, 31, 13); imm(0x23, 13, 13, 0)
    a.compare(12, 13, 3); a.branch(3, 'store'); imm(0x27, 14, 0, 255)
    a.label('store'); a.store(14, 15, 0, op=0x36)
    imm(0x2a, 26, 27, 0); imm(0x2a, 27, 29, 0)
    imm(0x27, 8, 8, 1); a.compare(8, 4, 4); a.branch(4, 'pixel')
    # rotate M and E rings
    imm(0x2a, 12, 20, 0); imm(0x2a, 20, 21, 0); imm(0x2a, 21, 22, 0); imm(0x2a, 22, 12, 0)
    imm(0x2a, 12, 23, 0); imm(0x2a, 23, 24, 0); imm(0x2a, 24, 28, 0); a.store(12, 1, 0)
    a.alu(30, 30, 4); imm(0x27, 7, 7, 1); a.compare(7, 5, 4); a.branch(4, 'row')
    return _epilogue(a, frame)


def bayer16_kernel(base, max_dimension=1024, allocation_size=0x30400):
    """Ordered 16x16 Bayer: white iff pixel >= table[(y&15)*16 + (x&15)]."""
    a = Code(); imm = a.immediate
    frame = _prologue(a, base, max_dimension, allocation_size)
    a.const(24, base+BAYER16_TABLE)
    imm(0x27, 7, 0, 0); imm(0x2a, 30, 3, 0)
    a.label('row')
    imm(0x29, 31, 7, 15); imm(0x2e, 31, 31, 4); a.alu(31, 24, 31)
    imm(0x27, 8, 0, 0)
    a.label('pixel')
    a.alu(15, 30, 8); imm(0x23, 12, 15, 0)
    imm(0x29, 13, 8, 15); a.alu(13, 31, 13); imm(0x23, 13, 13, 0)
    a.compare(12, 13, 3); imm(0x27, 14, 0, 0); a.branch(3, 'store'); imm(0x27, 14, 0, 255)
    a.label('store'); a.store(14, 15, 0, op=0x36)
    imm(0x27, 8, 8, 1); a.compare(8, 4, 4); a.branch(4, 'pixel')
    a.alu(30, 30, 4); imm(0x27, 7, 7, 1); a.compare(7, 5, 4); a.branch(4, 'row')
    return _epilogue(a, frame)


def _copy_row(a, src, dst, tag):
    imm = a.immediate
    imm(0x2a, 12, src, 0); imm(0x2a, 13, dst, 0); imm(0x2a, 14, 4, 0)
    a.label(f'copy_{tag}'); imm(0x23, 15, 12, 0); a.store(15, 13, 0, op=0x36)
    imm(0x27, 12, 12, 1); imm(0x27, 13, 13, 1)
    imm(0x27, 14, 14, -1); imm(0x2f, 1, 14, 0); a.branch(4, f'copy_{tag}')


def _blur5(a, taps, get, tmp, out):
    """out = t0*(A+E) + t1*(B+D) + t2*C for taps (1,4,6) or (1,7,16).
    get(reg, i) returns the register holding tap i (0..4), loading it into reg
    if needed. Uses r12, r13 and tmp."""
    alu = a.alu; imm = a.immediate
    alu(12, get(12, 0), get(13, 4)); alu(13, get(13, 1), get(tmp, 3))
    if taps == (1, 4, 6):
        imm(0x2e, 13, 13, 2); alu(12, 12, 13)
        c = get(13, 2); imm(0x2e, tmp, c, 2); imm(0x2e, 13, c, 1); alu(12, 12, 13); alu(out, 12, tmp)
    elif taps == (1, 7, 16):
        imm(0x2e, tmp, 13, 3); alu(13, tmp, 13, 2); alu(12, 12, 13)
        imm(0x2e, 13, get(13, 2), 4); alu(out, 12, 13)
    else:
        raise ValueError('unsupported blur taps')


def halftone_kernel(base, max_dimension=1024, allocation_size=0x30400, period=6, taps=(1, 4, 6),
                    table=HALFTONE_TABLE):
    """Binomial 5x5 blur compared against a period x period cosine screen
    (update 35: also HALFTONE 5X5 with the [1 7 16 7 1] / 32 blur).

    Output rows are delayed in a three-row ring and written back once no
    later row needs their original input. V row (u32) has two replicated
    cells on each side. Scratch use: 3w + 3 + 4(w+4) <= 12w for w >= 8.
    """
    a = Code(); imm = a.immediate
    frame = _prologue(a, base, max_dimension, allocation_size)
    imm(0x2a, 20, 6, 0); a.alu(21, 20, 4); a.alu(22, 21, 4)      # ring: old, mid, cur
    a.alu(23, 22, 4); imm(0x27, 23, 23, 3 + 8)
    a.const(12, 0xfffffffc); a.alu(23, 23, 12, 3)                   # r23=&V[0], aligned
    a.const(24, base+table)
    imm(0x27, 7, 0, 0); imm(0x27, 25, 5, -1)                        # y, h-1
    imm(0x27, 26, 0, 0)                                             # y mod 6
    a.label('row')
    # clamped input rows y-2..y+2 -> r15..r19
    for reg, dy in zip((15, 16, 17, 18, 19), (-2, -1, 0, 1, 2)):
        imm(0x27, 12, 7, dy)
        if dy < 0:
            imm(0x2f, 11, 12, 0); a.branch(4, f'clamp_ok{reg}'); imm(0x27, 12, 0, 0)
        else:
            a.compare(12, 25, 13); a.branch(4, f'clamp_ok{reg}'); imm(0x2a, 12, 25, 0)
        a.label(f'clamp_ok{reg}')
        a.alu(12, 12, 4, 0x306); a.alu(reg, 3, 12)
    # vertical pass
    imm(0x2a, 29, 23, 0); imm(0x2a, 14, 4, 0)
    a.label('vertical')
    _blur5(a, taps, lambda reg, i: imm(0x23, reg, 15+i, 0) or reg, 27, 12); a.store(12, 29)
    for reg in (15, 16, 17, 18, 19): imm(0x27, reg, reg, 1)
    imm(0x27, 29, 29, 4); imm(0x27, 14, 14, -1); imm(0x2f, 1, 14, 0); a.branch(4, 'vertical')
    # replicate edges: V[-2]=V[-1]=V[0], V[w]=V[w+1]=V[w-1]
    imm(0x21, 12, 23, 0); a.store(12, 23, -4); a.store(12, 23, -8)
    imm(0x21, 12, 29, -4); a.store(12, 29, 0); a.store(12, 29, 4)
    # horizontal pass + screen into ring row r22
    imm(0x27, 12, 0, period*4); a.alu(31, 26, 12, 0x306); a.alu(31, 24, 31)   # table row
    imm(0x2a, 28, 31, 0); imm(0x27, 27, 0, 0)                           # cell ptr, x mod 6
    imm(0x21, 15, 23, -8); imm(0x21, 16, 23, -4); imm(0x21, 17, 23, 0); imm(0x21, 18, 23, 4)
    imm(0x27, 29, 23, 8); imm(0x2a, 30, 22, 0); imm(0x2a, 14, 4, 0)
    a.label('horizontal')
    imm(0x21, 19, 29, 0)
    _blur5(a, taps, lambda reg, i: 15+i, 8, 12)
    imm(0x21, 13, 28, 0); a.compare(12, 13, 2)
    imm(0x27, 12, 0, 0); a.branch(3, 'dot'); imm(0x27, 12, 0, 255)
    a.label('dot'); a.store(12, 30, 0, op=0x36)
    imm(0x2a, 15, 16, 0); imm(0x2a, 16, 17, 0); imm(0x2a, 17, 18, 0); imm(0x2a, 18, 19, 0)
    imm(0x27, 29, 29, 4); imm(0x27, 30, 30, 1)
    imm(0x27, 27, 27, 1); imm(0x27, 28, 28, 4)
    imm(0x2f, 1, 27, period); a.branch(4, 'cell_ok'); imm(0x27, 27, 0, 0); imm(0x2a, 28, 31, 0)
    a.label('cell_ok')
    imm(0x27, 14, 14, -1); imm(0x2f, 1, 14, 0); a.branch(4, 'horizontal')
    # write back row y-2 (no longer an input), then rotate ring
    imm(0x2f, 4, 7, 2); a.branch(4, 'no_writeback')
    imm(0x27, 12, 7, -2); a.alu(12, 12, 4, 0x306); a.alu(2, 3, 12)
    _copy_row(a, 20, 2, 'old')
    a.label('no_writeback')
    imm(0x2a, 12, 20, 0); imm(0x2a, 20, 21, 0); imm(0x2a, 21, 22, 0); imm(0x2a, 22, 12, 0)
    imm(0x27, 26, 26, 1); imm(0x2f, 1, 26, period); a.branch(4, 'ymod_ok'); imm(0x27, 26, 0, 0)
    a.label('ymod_ok')
    imm(0x27, 7, 7, 1); a.compare(7, 5, 4); a.branch(4, 'row')
    # flush rows h-2 (ring old) and h-1 (ring mid)
    imm(0x2f, 4, 5, 2); a.branch(4, 'last_row')
    imm(0x27, 12, 5, -2); a.alu(12, 12, 4, 0x306); a.alu(2, 3, 12)
    _copy_row(a, 20, 2, 'tail_old')
    a.label('last_row')
    a.alu(12, 25, 4, 0x306); a.alu(2, 3, 12)
    _copy_row(a, 21, 2, 'tail_mid')
    return _epilogue(a, frame)


def halftone_table(period=6):
    t = halftone_thresholds(period)
    return struct.pack(f'<{len(t)}I', *t)


# ---- Update 19: sliced halftone for the live preview -------------------------
HALFTONE_SLICED = 0x9200            # after the (hidden) Bayer 16x16 kernel (0x9000..0x91f4)
SLICE_RING, SLICE_SAVE, SLICE_V = 0, 3*320, 1600   # offsets in PREVIEW_SCRATCH


def halftone_slice_kernel(base, allocation_size=0x30400, max_rows=64):
    """Sliced-worker ABI: r3=image (snapshot, in place), r4=w (<=320), r5=h,
    r6=preview scratch, r7=first row, r8=rows (2..max_rows). Produces exactly the
    rows of halftone(snapshot). Rows y0-2, y0-1 were overwritten by the previous
    slice; their source copies are kept in scratch (SAVE) between slices."""
    from responsive_effects_patch import PREVIEW_SCRATCH
    a = Code(); imm = a.immediate
    saved = tuple(r for r in range(2, 32) if r not in (9, 11))
    frame = len(saved)*4+16
    imm(0x27, 1, 1, -frame)
    for i, r in enumerate(saved): a.store(r, 1, 16+i*4)
    imm(0x2f, 4, 4, MIN_WIDTH); a.branch(4, 'reject'); imm(0x2f, 2, 4, 320); a.branch(4, 'reject')
    imm(0x2f, 4, 5, 3); a.branch(4, 'reject'); imm(0x2f, 2, 5, 1024); a.branch(4, 'reject')
    imm(0x2f, 4, 8, 2); a.branch(4, 'reject'); imm(0x2f, 2, 8, max_rows); a.branch(4, 'reject')
    a.compare(7, 5, 3); a.branch(4, 'reject')
    a.const(12, base+PREVIEW_SCRATCH); a.compare(6, 12, 1); a.branch(4, 'reject')
    # The worker's private snapshot (WORK_IMAGE, 320x240) is the only valid image.
    from responsive_effects_patch import WORK_IMAGE
    a.const(12, base+WORK_IMAGE); a.compare(3, 12, 1); a.branch(4, 'reject')
    a.alu(13, 4, 5, 0x306); a.const(12, 320*240); a.compare(13, 12, 2); a.branch(4, 'reject')
    # y_end = min(y0+n, h) -> sp+0 ; y0 -> sp+4
    a.alu(12, 7, 8); a.compare(12, 5, 5); a.branch(4, 'end_ok'); imm(0x2a, 12, 5, 0)
    a.label('end_ok'); a.store(12, 1, 0); a.store(7, 1, 4)
    imm(0x2a, 20, 6, 0); a.alu(21, 20, 4); a.alu(22, 21, 4)             # ring old, mid, cur
    imm(0x27, 30, 6, SLICE_SAVE)                                         # save rows (y0-2, y0-1)
    imm(0x27, 23, 6, SLICE_V+8)                                          # &V[0]
    a.const(24, base+HALFTONE_TABLE)
    imm(0x27, 25, 5, -1)                                                 # h-1
    imm(0x27, 12, 0, 6); a.alu(26, 7, 12, 0x30a); a.alu(26, 26, 12, 0x306); a.alu(26, 7, 26, 2)   # y0 mod 6
    imm(0x2a, 7, 7, 0)                                                   # y = y0
    a.label('row')
    for reg, dy in zip((15, 16, 17, 18, 19), (-2, -1, 0, 1, 2)):
        imm(0x27, 12, 7, dy)
        if dy < 0:
            imm(0x2f, 11, 12, 0); a.branch(4, f'nonneg{reg}'); imm(0x27, 12, 0, 0)
            a.label(f'nonneg{reg}')
            imm(0x21, 13, 1, 4); a.compare(12, 13, 12); a.branch(3, f'in_image{reg}')   # r < y0 ?
            # rows y0-2 / y0-1 come from SAVE (only reachable when y0 >= 2)
            a.alu(13, 13, 12, 2); imm(0x27, 14, 0, 2); a.alu(13, 14, 13, 2)             # 2-(y0-r) = 0/1
            a.alu(13, 13, 4, 0x306); a.alu(reg, 30, 13); a.branch(0, f'row_ok{reg}')
            a.label(f'in_image{reg}')
        else:
            a.compare(12, 25, 13); a.branch(4, f'clamp_ok{reg}'); imm(0x2a, 12, 25, 0)
            a.label(f'clamp_ok{reg}')
        a.alu(12, 12, 4, 0x306); a.alu(reg, 3, 12)
        a.label(f'row_ok{reg}')
    imm(0x2a, 29, 23, 0); imm(0x2a, 14, 4, 0)
    a.label('vertical')
    imm(0x23, 12, 15, 0); imm(0x23, 13, 19, 0); a.alu(12, 12, 13)
    imm(0x23, 13, 16, 0); imm(0x23, 27, 18, 0); a.alu(13, 13, 27); imm(0x2e, 13, 13, 2)
    a.alu(12, 12, 13); imm(0x23, 13, 17, 0); imm(0x2e, 27, 13, 2); imm(0x2e, 13, 13, 1)
    a.alu(12, 12, 13); a.alu(12, 12, 27); a.store(12, 29)
    for reg in (15, 16, 17, 18, 19): imm(0x27, reg, reg, 1)
    imm(0x27, 29, 29, 4); imm(0x27, 14, 14, -1); imm(0x2f, 1, 14, 0); a.branch(4, 'vertical')
    imm(0x21, 12, 23, 0); a.store(12, 23, -4); a.store(12, 23, -8)
    imm(0x21, 12, 29, -4); a.store(12, 29, 0); a.store(12, 29, 4)
    imm(0x27, 12, 0, 24); a.alu(31, 26, 12, 0x306); a.alu(31, 24, 31)
    imm(0x2a, 28, 31, 0); imm(0x27, 27, 0, 0)
    imm(0x21, 15, 23, -8); imm(0x21, 16, 23, -4); imm(0x21, 17, 23, 0); imm(0x21, 18, 23, 4)
    imm(0x27, 29, 23, 8); imm(0x2a, 2, 22, 0); imm(0x2a, 14, 4, 0)
    a.label('horizontal')
    imm(0x21, 19, 29, 0)
    a.alu(12, 15, 19); a.alu(13, 16, 18); imm(0x2e, 13, 13, 2); a.alu(12, 12, 13)
    imm(0x2e, 13, 17, 2); a.alu(12, 12, 13); imm(0x2e, 13, 17, 1); a.alu(12, 12, 13)
    imm(0x21, 13, 28, 0); a.compare(12, 13, 2)
    imm(0x27, 12, 0, 0); a.branch(3, 'dot'); imm(0x27, 12, 0, 255)
    a.label('dot'); a.store(12, 2, 0, op=0x36)
    imm(0x2a, 15, 16, 0); imm(0x2a, 16, 17, 0); imm(0x2a, 17, 18, 0); imm(0x2a, 18, 19, 0)
    imm(0x27, 29, 29, 4); imm(0x27, 2, 2, 1)
    imm(0x27, 27, 27, 1); imm(0x27, 28, 28, 4)
    imm(0x2f, 1, 27, 6); a.branch(4, 'cell_ok'); imm(0x27, 27, 0, 0); imm(0x2a, 28, 31, 0)
    a.label('cell_ok')
    imm(0x27, 14, 14, -1); imm(0x2f, 1, 14, 0); a.branch(4, 'horizontal')
    # write back row y-2 if it belongs to this slice
    imm(0x27, 12, 7, -2); imm(0x21, 13, 1, 4); a.compare(12, 13, 12); a.branch(4, 'no_writeback')
    a.alu(12, 12, 4, 0x306); a.alu(2, 3, 12)
    _copy_row(a, 20, 2, 'old')
    a.label('no_writeback')
    imm(0x2a, 12, 20, 0); imm(0x2a, 20, 21, 0); imm(0x2a, 21, 22, 0); imm(0x2a, 22, 12, 0)
    imm(0x27, 26, 26, 1); imm(0x2f, 1, 26, 6); a.branch(4, 'ymod_ok'); imm(0x27, 26, 0, 0)
    a.label('ymod_ok')
    imm(0x27, 7, 7, 1); imm(0x21, 12, 1, 0); a.compare(7, 12, 4); a.branch(4, 'row')
    # keep the source of the slice's last two rows for the next slice, then flush.
    # A one-row slice (y_end = y0+1) keeps SAVE[1] (source of y0-1) as the new SAVE[0].
    imm(0x21, 12, 1, 0); imm(0x21, 13, 1, 4); a.alu(13, 12, 13, 2)
    imm(0x2f, 11, 13, 2); a.branch(4, 'two_rows')
    a.alu(19, 30, 4); imm(0x2a, 2, 30, 0)
    _copy_row(a, 19, 2, 'shift_save')
    a.branch(0, 'save_last')
    a.label('two_rows')
    imm(0x21, 12, 1, 0); imm(0x27, 13, 12, -2); a.alu(13, 13, 4, 0x306); a.alu(19, 3, 13)
    imm(0x2a, 2, 30, 0)
    _copy_row(a, 19, 2, 'save0')
    a.label('save_last')
    imm(0x21, 12, 1, 0); imm(0x27, 13, 12, -1); a.alu(13, 13, 4, 0x306); a.alu(19, 3, 13)
    a.alu(2, 30, 4)
    _copy_row(a, 19, 2, 'save1')
    # flush output rows still held in the ring (only rows of this slice)
    imm(0x21, 12, 1, 0); imm(0x21, 13, 1, 4); a.alu(13, 12, 13, 2)
    imm(0x2f, 12, 13, 2); a.branch(4, 'flush_last')
    imm(0x27, 13, 12, -2); a.alu(13, 13, 4, 0x306); a.alu(2, 3, 13)
    _copy_row(a, 20, 2, 'tail_old')
    a.label('flush_last')
    imm(0x21, 12, 1, 0); imm(0x27, 13, 12, -1); a.alu(13, 13, 4, 0x306); a.alu(2, 3, 13)
    _copy_row(a, 21, 2, 'tail_mid')
    imm(0x27, 11, 0, 0); a.branch(0, 'restore')
    a.label('reject'); imm(0x27, 11, 0, 8)
    a.label('restore')
    for i, r in enumerate(saved): imm(0x21, r, 1, 16+i*4)
    imm(0x27, 1, 1, frame); a.emit(0x44004800)
    code = a.finish()
    if len(code) > 0x700: raise ValueError('sliced halftone overflow')
    return code
