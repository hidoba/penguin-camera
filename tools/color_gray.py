"""Warm 'strong orange filter' black-and-white conversion (update 10).

gray = 0.55 R + 0.40 G + 0.05 B on the camera's gamma-encoded RGB, rewritten in
YCbCr (full-range JFIF): gray = Y + 0.4855 (Cr-128) - 0.0491 (Cb-128), in Q8.
Buffers are the stock format 4: Y plane (w*h) followed by an interleaved 4:2:0
chroma plane with one w-byte row per two image rows, Cr at even and Cb at odd
bytes (verified from the yellow stock camera icon, resource 072, in RAM).
"""
from live_preview_patch import Code

KR, KB = 124, 13          # round(0.48545*256), round(0.049054*256)
ORANGE = 0xac00
MAX_CODE = 0x400


def reference(buf, w, h):
    """Host oracle: returns the converted Y plane (chroma untouched)."""
    if w < 2 or w % 2 or h % 2: return bytes(buf[:w*h])
    y, c = bytearray(buf[:w*h]), buf[w*h:w*h+w*h//2]
    for row in range(h):
        crow = (row >> 1)*w
        for x in range(0, w, 2):
            adj = (KR*(c[crow+x]-128) - KB*(c[crow+x+1]-128) + 128) >> 8
            for i in (row*w+x, row*w+x+1):
                y[i] = min(255, max(0, y[i]+adj))
    return bytes(y)


def orange(base, allocation_size):
    """r3=Y, r4=width, r5=height; chroma at r3+w*h. Preserves every register."""
    a = Code(); imm = a.immediate
    saved = tuple(range(12, 24))   # r12..r23
    frame = len(saved)*4
    imm(0x27, 1, 1, -frame)
    for i, r in enumerate(saved): a.store(r, 1, i*4)
    imm(0x2f, 4, 4, 2); a.branch(4, 'done')
    imm(0x2f, 2, 4, 1024); a.branch(4, 'done')
    imm(0x2f, 4, 5, 2); a.branch(4, 'done')
    imm(0x2f, 2, 5, 1024); a.branch(4, 'done')
    imm(0x29, 12, 4, 1); imm(0x2f, 1, 12, 0); a.branch(4, 'done')
    imm(0x29, 12, 5, 1); imm(0x2f, 1, 12, 0); a.branch(4, 'done')
    a.alu(12, 4, 5, 0x306); a.alu(13, 3, 12)              # r13 = chroma
    imm(0x2e, 14, 12, 0x41); a.alu(14, 13, 14)            # r14 = end of chroma
    a.const(15, 0x02090000); a.compare(3, 15, 4); a.branch(4, 'done')
    a.const(15, 0x02200000); a.compare(14, 15, 2); a.branch(4, 'done')
    a.compare(14, 1, 2); a.branch(4, 'done')
    a.const(15, base+allocation_size); a.compare(3, 15, 3); a.branch(4, 'disjoint')
    a.const(15, base); a.compare(14, 15, 2); a.branch(4, 'done')
    a.label('disjoint')
    # Update 19: one correction per 2x2 block (rows y, y+1 share a chroma row),
    # clamped only in the direction of the correction. Same results, ~35% fewer
    # instructions than per-pair processing.
    imm(0x2a, 15, 3, 0); imm(0x27, 16, 0, 0)                 # row ptr (even row), row
    a.label('row')
    imm(0x2e, 17, 16, 0x41); a.alu(17, 17, 4, 0x306); a.alu(17, 13, 17)   # chroma row
    a.alu(23, 15, 4)                                          # odd row ptr
    imm(0x27, 18, 0, 0)
    a.label('pair')
    a.alu(19, 17, 18); imm(0x23, 20, 19, 0); imm(0x23, 21, 19, 1)
    imm(0x27, 20, 20, -128); imm(0x27, 21, 21, -128)
    imm(0x27, 22, 0, KR); a.alu(20, 20, 22, 0x306)
    imm(0x27, 22, 0, KB); a.alu(21, 21, 22, 0x306)
    a.alu(20, 20, 21, 2); imm(0x27, 20, 20, 128); imm(0x2e, 20, 20, 0x88)   # adj
    imm(0x2f, 0, 20, 0); a.branch(4, 'next_pair')              # neutral block: nothing to do
    a.alu(19, 15, 18); a.alu(22, 23, 18)
    imm(0x2f, 12, 20, 0); a.branch(4, 'down')
    for k, (ptr, off) in enumerate(((19, 0), (19, 1), (22, 0), (22, 1))):
        imm(0x23, 21, ptr, off); a.alu(21, 21, 20)
        imm(0x2f, 5, 21, 255); a.branch(4, f'up_ok{k}'); imm(0x27, 21, 0, 255)
        a.label(f'up_ok{k}'); a.store(21, ptr, off, op=0x36)
    a.branch(0, 'next_pair')
    a.label('down')
    for k, (ptr, off) in enumerate(((19, 0), (19, 1), (22, 0), (22, 1))):
        imm(0x23, 21, ptr, off); a.alu(21, 21, 20)
        imm(0x2f, 11, 21, 0); a.branch(4, f'down_ok{k}'); imm(0x27, 21, 0, 0)
        a.label(f'down_ok{k}'); a.store(21, ptr, off, op=0x36)
    a.label('next_pair')
    imm(0x27, 18, 18, 2); a.compare(18, 4, 4); a.branch(4, 'pair')
    a.alu(15, 23, 4); imm(0x27, 16, 16, 2); a.compare(16, 5, 4); a.branch(4, 'row')
    a.label('done')
    for i, r in enumerate(saved): imm(0x21, r, 1, i*4)
    imm(0x27, 1, 1, frame); a.emit(0x44004800)
    code = a.finish()
    if len(code) > MAX_CODE: raise ValueError('orange conversion overflow')
    return code
