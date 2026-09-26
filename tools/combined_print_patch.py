"""Volatile photo preview/print integration; NOT a persistent firmware image.

Only the photo capture path is rerouted away from file saving. Other screens
are not covered by a global SD-write prohibition. Stock thermal code is intact.
"""
import struct
from live_preview_patch import Code, build as preview_build, BIAS, START, STATE, TABLES, END
from build_gray_candidate import branch

ENTRY = 0
TRANSFORM = 0x200
LUT = 0x1000
PRINT_STATE = STATE + 16  # attempts, status, pointer, width, height, enabled, mode
DRIVER = 0x4cf20


def transform(base,diffusion=(),scratch=None,allocation_size=END,max_dimension=672,after=None,before=None,gray=None,reject_flush=False):
    """r3 Y, r4 width, r5 height. Return r11=0 or 8; all other regs intact.

    Decoder already invalidates the DMA destination before this call. This
    leaf only edits CPU memory; its wrapper flushes before stock submission.
    """
    if not 1<=max_dimension<=1024:raise ValueError('unsupported dimension limit')
    a = Code(); imm = a.immediate
    saved = (3, 4, 5, 6, 7, 8, 12, 13, 15) + ((9,) if diffusion else ())
    frame = 48 if diffusion else 40
    imm(0x27, 1, 1, -frame)
    for i, r in enumerate(saved): a.store(r, 1, i*4)
    # Bounded supported printer dimensions. Do not truncate high bits.
    for r in (4, 5):
        imm(0x2f, 4, r, 1); a.branch(4, 'reject')
        imm(0x2f, 2, r, max_dimension); a.branch(4, 'reject')
    imm(0x2f, 0, 4, 384); a.branch(4, 'dimensions_ok')
    imm(0x2f, 1, 5, 384); a.branch(4, 'reject')
    a.label('dimensions_ok')
    a.const(12, 0x02090000); a.compare(3, 12, 4); a.branch(4, 'reject')
    a.const(12, 0x02200000); a.compare(3, 12, 3); a.branch(4, 'reject')
    imm(0x29, 12, 3, 31); imm(0x2f, 1, 12, 0); a.branch(4, 'reject')
    a.alu(12, 4, 5, 0x306); a.alu(13, 3, 12)
    a.const(15, 0x02200000); a.compare(13, 15, 2); a.branch(4, 'reject')
    # Reject overlap with our allocation and current call stack.
    for lower, upper in ((base, base+allocation_size),):
        a.const(15, upper); a.compare(3, 15, 3); a.branch(4, 'allocation_ok')
        a.const(15, lower); a.compare(13, 15, 2); a.branch(4, 'reject')
    a.label('allocation_ok')
    a.compare(13, 1, 2); a.branch(4, 'reject')
    a.const(15, base+STATE); imm(0x21, 6, 15, 0); imm(0x21, 7, 15, 4)
    imm(0x2f, 2, 6, 1); a.branch(4, 'reject')
    imm(0x2f, 2, 7, 2+len(diffusion)); a.branch(4, 'reject')
    # Snapshot state once; no mode changes halfway through an image.
    a.const(15, base+PRINT_STATE)
    a.store(6, 15, 20); a.store(7, 15, 24)
    if before is not None:
        # Optional pre-conversion of the whole image (callee preserves all
        # registers; r9 is saved by this routine's diffusion prologue).
        if not diffusion: raise ValueError('pre-conversion requires the saved-link variant')
        a.call(base+TRANSFORM, before)
    imm(0x2f, 0, 6, 0); a.branch(4, 'gray')
    if diffusion:
        if scratch is None: raise ValueError('Diffusion requires private print scratch')
        imm(0x2f,3,7,3); a.branch(4,'diffusion')
    imm(0x2e, 7, 7, 6); a.const(15, base+TABLES); a.alu(15, 15, 7)
    imm(0x27, 6, 0, 0)
    a.label('row')
    imm(0x29, 8, 6, 7); imm(0x2e, 8, 8, 3); a.alu(8, 15, 8)
    imm(0x27, 7, 0, 0)
    a.label('pixel')
    imm(0x29, 11, 7, 7); a.alu(11, 8, 11); imm(0x23, 11, 11, 0)
    imm(0x23, 12, 3, 0); a.compare(12, 11, 3)
    imm(0x27, 12, 0, 0); a.branch(3, 'store')
    imm(0x27, 12, 0, 255)
    a.label('store'); a.store(12, 3, op=0x36)
    imm(0x27, 3, 3, 1); imm(0x27, 7, 7, 1)
    a.compare(7, 4, 4); a.branch(4, 'pixel')
    imm(0x27, 6, 6, 1); a.compare(6, 5, 4); a.branch(4, 'row')
    a.branch(0, 'success')
    a.label('gray')
    if gray is not None:
        # Measured tone/load/heat compensation for 384-dot print buffers; the
        # old 190/255 table remains the fallback for any other geometry.
        imm(0x2f, 1, 5, 384); a.branch(4, 'legacy_gray')
        a.call(base+TRANSFORM, gray); imm(0x2f, 0, 11, 0); a.branch(4, 'success')
        a.label('legacy_gray')
    a.const(15, base+LUT)
    a.label('gray_pixel')
    imm(0x23, 11, 3, 0); a.alu(11, 15, 11); imm(0x23, 11, 11, 0)
    a.store(11, 3, op=0x36); imm(0x27, 3, 3, 1)
    imm(0x27, 12, 12, -1); imm(0x2f, 1, 12, 0); a.branch(4, 'gray_pixel')
    if diffusion:
        a.branch(0,'success')
        a.label('diffusion'); a.const(6,scratch)
        for i,target in enumerate(diffusion):
            imm(0x2f,1,7,3+i); a.branch(4,f'next_diffusion{i}')
            a.call(base+TRANSFORM,target); a.branch(0,'diffusion_ret' if reject_flush else 'restore')
            a.label(f'next_diffusion{i}')
        a.branch(0,'reject')
        if reject_flush:
            # Update 32: `before` already rewrote Y; publish it even if the effect
            # rejects (r11 kept; sp+44 is a free slot of this frame).
            a.label('diffusion_ret'); imm(0x2f,0,11,0); a.branch(4,'restore')
            imm(0x21,3,1,0); imm(0x21,4,1,4); imm(0x21,5,1,8); a.alu(4,4,5,0x306)
            a.store(11,1,44); a.call(base+TRANSFORM,BIAS+0x29790); imm(0x21,11,1,44); a.branch(0,'restore')
    a.label('success'); imm(0x27, 11, 0, 0); a.branch(0, 'restore')
    a.label('reject'); imm(0x27, 11, 0, 8)
    a.label('restore')
    for i, r in enumerate(saved): imm(0x21, r, 1, i*4)
    imm(0x27, 1, 1, frame)
    if after is not None:
        # Optional post-processing on success with the original r3..r5; the
        # callee preserves every register. Save our link register around it.
        imm(0x2f, 1, 11, 0); a.branch(4, 'after_done')
        imm(0x27, 1, 1, -4); a.store(9, 1, 0)
        a.call(base+TRANSFORM, after)
        imm(0x21, 9, 1, 0); imm(0x27, 1, 1, 4)
        a.label('after_done')
    # Diagnostic metadata does not invoke a print or touch device registers.
    imm(0x27, 1, 1, -8); a.store(12, 1, 0); a.store(13, 1, 4)
    a.const(12, base+PRINT_STATE); imm(0x21, 13, 12, 0)
    imm(0x27, 13, 13, 1); a.store(13, 12, 0)
    a.store(11, 12, 4); a.store(3, 12, 8); a.store(4, 12, 12); a.store(5, 12, 16)
    imm(0x21, 12, 1, 0); imm(0x21, 13, 1, 4); imm(0x27, 1, 1, 8)
    a.emit(0x44004800)
    code = a.finish()
    assert len(code) <= LUT-TRANSFORM
    return code


def entry(base):
    a = Code(); imm = a.immediate
    saved = (3, 4, 5, 6, 7, 8, 9, 12, 13, 15)
    imm(0x27, 1, 1, -40)
    for i, r in enumerate(saved): a.store(r, 1, i*4)
    a.call(base+ENTRY, base+TRANSFORM)
    imm(0x2f, 1, 11, 0); a.branch(4, 'reject')
    a.alu(4, 4, 5, 0x306)
    a.call(base+ENTRY, BIAS+0x29790)
    for i, r in enumerate(saved): imm(0x21, r, 1, i*4)
    imm(0x27, 1, 1, 40)
    a.emit(struct.unpack('<I', branch(base+len(a.words)*4, BIAS+DRIVER))[0])
    a.label('reject')
    for i, r in enumerate(saved): imm(0x21, r, 1, i*4)
    imm(0x27, 1, 1, 40); a.emit(0x44004800)
    code = a.finish(); assert len(code) < TRANSFORM
    return code


def build(base, original):
    previous, changes = preview_build(base, original)
    blob = bytearray(END)
    blob[START:] = previous[START:]
    for off, code in ((ENTRY, entry(base)), (TRANSFORM, transform(base))):
        blob[off:off+len(code)] = code
    blob[LUT:LUT+256] = bytes((v*190+127)//255 for v in range(256))
    # Fresh installation, not an upgrade of the old diagnostic wrappers.
    for patch in changes:
        off = patch['address']-BIAS
        patch['original'] = original[off:off+4].hex()
    replacements = (
        # Memory-print routine: bypass stock binary enhancement and let our
        # state select processing. r18=0 selects the unchanged gray driver.
        (0x4d908, 0xa64500ff, struct.pack('<I', 0x9e400000), 'memory print render route'),
        (0x4d978, 0x07fffd6a, struct.pack('<I', struct.unpack('<I', branch(BIAS+0x4d978, base+ENTRY))[0]|1<<26), 'print preprocessing'),
        # Photo handler only: force memory capture, require returned JPEG,
        # force printing even if stock UI initializes print/save to zero.
        (0xba38, 0x8c740005, struct.pack('<I', 0x9c600000), 'photo memory capture'),
        (0xba48, 0x8c6e007a, struct.pack('<I', 0x9c600001), 'photo print enabled'),
        (0xba54, 0x0400f1d9, branch(0xba54, 0xba98), 'fresh capture, not dithered retained frame'),
        (0xba98, 0x8cb4007a, struct.pack('<I', 0x9ca00001), 'retain JPEG in RAM'),
        (0xbb58, 0x8c72007a, struct.pack('<I', 0x9c600001), 'photo always print'),
        (0xbb64, 0x8c720005, struct.pack('<I', 0x9c600000), 'photo memory print'),
    )
    for off, expected, replacement, label in replacements:
        if original[off:off+4] != struct.pack('<I', expected):
            raise ValueError(f'Unexpected stock instruction {off:#x}')
        changes.append(dict(address=BIAS+off, original=original[off:off+4].hex(),
                            replacement=replacement.hex(), label=label))
    return bytes(blob), changes
