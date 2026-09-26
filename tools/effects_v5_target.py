"""Native OpenRISC kernels for update 05 (Bayer 8x8 + edges, Halftone).

ABI shared with the resident Halftone 4x4 / Error Diffusion 1D kernels: r3=Y (in place), r4=width,
r5=height, r6=scratch, r11=0 success / 8 reject, all other registers preserved.
No calls, allocation, cache maintenance or device access. Scratch must be one
of the payload's two known private scratch areas (preview: width <= 320).
Bit-exact against the host oracles in effects_v5.py.
"""
import struct
from live_preview_patch import Code
from responsive_effects_patch import PREVIEW_SCRATCH
from persistent_effects_patch import PRINT_SCRATCH
from effects_v5 import halftone_thresholds

# Formerly the retained 672-row print scratch; no longer referenced by any
# resident code (print uses PRINT_SCRATCH). Checked by the integration build.
HALFTONE = 0x9800
HALFTONE_TABLE = 0xa000
# Update 35: HALFTONE 5X5 (mode 9, in place of the hidden Bayer 16x16) in the free
# tail of the print-kernel slot 0x5000 (kernel 0x5000..0x53e0).
HALFTONE5 = 0x5400
HALFTONE5_TABLE = 0x5c00
HALFTONE5_END = 0x5c80
HALFTONE5_TAPS = (1, 7, 16)     # sigma 0.83 = 5/6
REGION = (0x9000, 0xa1c0)       # update-05 code region; must be free when apply() starts
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


