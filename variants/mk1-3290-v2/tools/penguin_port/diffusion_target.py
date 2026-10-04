"""Vendored penguin camera math/native generator. Adaptations: MK1 RAM bounds; explicit UV input for orange; larger sensor width. No hardware access."""
import math, struct, json
from pathlib import Path
from mk1_native import Code

TAPS = {
    'floyd': (16, ((1,0,7),(-1,1,3),(0,1,5),(1,1,1))),
    'atkinson': (8, ((1,0,1),(2,0,1),(-1,1,1),(0,1,1),(1,1,1),(0,2,1))),
    'stucki': (42, ((1,0,8),(2,0,4),(-2,1,2),(-1,1,4),(0,1,8),(1,1,4),(2,1,2),
                    (-2,2,1),(-1,2,2),(0,2,4),(1,2,2),(2,2,1))),
    'cracked': (2, ((1,0,1),(0,1,1))),
}

MAX_CODE = 0x1000

def make_kernel(mode, address, sliced=False, max_dimension=672,max_slice_rows=32,*,optimized=False):
    """r3=pixels, r4=width, r5=height, r6=3*width int32 scratch. r11=status.

    Caller must supply actual allocations of sufficient size; address guards
    cannot establish allocation ownership. All other registers are preserved.
    """
    if not 1<=max_dimension<=1536:raise ValueError('unsupported dimension limit')
    if not 1<=max_slice_rows<=128:raise ValueError('unsupported slice budget')
    denominator, taps = TAPS[mode]
    if optimized and mode=='stucki':raise ValueError('Stucki optimization not supported')
    if address%4 or not 0x02000000<=address<=0x027fec00-MAX_CODE:
        raise ValueError('Unapproved code address')
    a=Code(); imm=a.immediate
    saved=tuple(r for r in range(2,31) if r!=11)
    frame=128 if sliced else len(saved)*4
    imm(0x27,1,1,-frame)
    for i,r in enumerate(saved): a.store(r,1,i*4)
    for r in (4,5):
        imm(0x2f,4,r,1); a.branch(4,'reject')
        imm(0x2f,2,r,max_dimension); a.branch(4,'reject')
    if sliced:
        # r7=start row, r8=1..32 rows. Error rows belong to this snapshot and
        # must survive between calls. Caller guarantees contiguous slices.
        a.compare(7,5,3); a.branch(4,'reject')
        imm(0x2f,4,8,1); a.branch(4,'reject')
        imm(0x2f,2,8,max_slice_rows); a.branch(4,'reject')
        a.alu(12,7,8); a.compare(12,5,5); a.branch(4,'end_ok')
        imm(0x2a,12,5,0)
        a.label('end_ok'); a.store(12,1,112)
    imm(0x29,12,6,3); imm(0x2f,1,12,0); a.branch(4,'reject')
    a.alu(12,4,5,0x306); a.alu(13,3,12)  # image exclusive end
    imm(0x27,14,0,12); a.alu(14,4,14,0x306); a.alu(15,6,14)  # scratch end
    for pointer,end in ((3,13),(6,15)):
        a.const(16,0x020e0000); a.compare(pointer,16,4); a.branch(4,'reject')
        a.const(16,0x027fec00); a.compare(pointer,16,3); a.branch(4,'reject')
        a.compare(end,pointer,5); a.branch(4,'reject')  # overflow/empty
        a.const(16,0x027fec00); a.compare(end,16,2); a.branch(4,'reject')
        a.compare(end,1,2); a.branch(4,'reject')
        a.const(16,address+MAX_CODE); a.compare(pointer,16,3); a.branch(4,f'code_ok{pointer}')
        a.const(16,address); a.compare(end,16,2); a.branch(4,'reject')
        a.label(f'code_ok{pointer}')
    a.compare(3,15,3); a.branch(4,'disjoint')
    a.compare(6,13,4); a.branch(4,'reject')
    a.label('disjoint')
    if sliced:
        imm(0x2f,1,7,0); a.branch(4,'clear_done')
    # Clear exactly three scratch rows, even if caller reused dirty storage.
    imm(0x2a,16,6,0)
    a.label('clear'); a.store(0,16); imm(0x27,16,16,4)
    a.compare(16,15,4); a.branch(4,'clear')
    if sliced: a.label('clear_done')
    imm(0x2e,29,4,2)
    imm(0x2a,17,6,0); a.alu(18,17,29); a.alu(19,18,29)
    imm(0x2a,16,3,0); imm(0x27,12,0,0); imm(0x27,26,0,denominator)
    if sliced:
        imm(0x27,28,0,3); a.alu(12,7,28,0x30a); a.alu(12,12,28,0x306)
        a.alu(12,7,12,2)
        imm(0x2f,0,12,0); a.branch(4,'ring_ready')
        a.label('rotate_ring')
        imm(0x2a,28,17,0); imm(0x2a,17,18,0); imm(0x2a,18,19,0); imm(0x2a,19,28,0)
        imm(0x27,12,12,-1); imm(0x2f,1,12,0); a.branch(4,'rotate_ring')
        a.label('ring_ready'); a.alu(28,7,4,0x306); a.alu(16,3,28); imm(0x2a,12,7,0)
    if optimized:
        # r3/r6 are no longer pointers after setup; preserve constants rather
        # than constructing both clamp/threshold values inside every pixel.
        imm(0x2a,3,0,255*256);imm(0x2a,6,0,128*256)
    a.label('row')
    imm(0x27,15,0,1); imm(0x27,14,0,0)
    if mode!='cracked':
        imm(0x29,28,12,1); imm(0x2f,0,28,0); a.branch(4,'forward')
        imm(0x27,15,0,-1); imm(0x27,14,4,-1)
    a.label('forward'); imm(0x27,13,0,0)
    if optimized:imm(0x2e,26,15,2)  # signed horizontal error-buffer stride
    a.label('pixel')
    a.alu(30,16,14); imm(0x23,20,30,0); imm(0x2e,20,20,8)
    imm(0x2e,24,14,2); a.alu(24,17,24); imm(0x21,27,24,0)
    a.alu(20,20,27)
    imm(0x2f,11,20,0); a.branch(4,'nonnegative')
    imm(0x27,20,0,0)
    a.label('nonnegative')
    if not optimized:a.const(28,255*256)
    a.compare(20,3 if optimized else 28,13); a.branch(4,'clamped')
    imm(0x2a,20,3 if optimized else 28,0)
    a.label('clamped')
    if not optimized:a.const(28,128*256)
    a.compare(20,6 if optimized else 28,3); imm(0x27,21,0,0); a.branch(3,'quantized')
    imm(0x27,21,0,255)
    a.label('quantized'); a.store(21,30,op=0x36)
    imm(0x2e,21,21,8); a.alu(20,20,21,2)
    # Compute each distinct weighted error once, not once per tap. In Stucki
    # twelve destinations use only four weights; Atkinson uses only one.
    weighted=dict(zip(sorted({tap[2] for tap in taps}),(2,7,8,10)))
    if optimized:
        # All tap weights are positive: one sign correction serves every
        # numerator, with exactly C's signed truncation toward zero.
        imm(0x2e,25,20,0x9f);imm(0x29,25,25,denominator-1)
    for weight,reg in weighted.items():
        if weight in (1,2,4,8):
            imm(0x2e,reg,20,{1:0,2:1,4:2,8:3}[weight])
        else:
            imm(0x27,reg,0,weight); a.alu(reg,20,reg,0x306)
        if denominator in (2,8,16):
            # Arithmetic right shift rounds down; add divisor-1 only for a
            # negative numerator to match C's signed truncation toward zero.
            if optimized:a.alu(reg,reg,25)
            else:
                imm(0x2f,11,reg,0); a.branch(4,f'div_positive{weight}')
                imm(0x27,reg,reg,denominator-1)
                a.label(f'div_positive{weight}')
            imm(0x2e,reg,reg,0x80+{2:1,8:3,16:4}[denominator])
        else: a.alu(reg,reg,26,0x309)
    if optimized:
        # Reuse this pixel's error-row addresses across taps. r24 still points
        # to the current row's error cell loaded above; bounds checks remain.
        imm(0x2e,28,14,2);a.alu(22,18,28)
        if any(dy==2 for _,dy,_ in taps):a.alu(23,19,28)
        for dy in sorted({tap[1] for tap in taps}):
            if dy:
                imm(0x27,27,12,dy);a.compare(27,5,3);a.branch(4,f'fast_row_done{dy}')
            for index,(dx,tap_y,weight) in enumerate(taps):
                if tap_y!=dy:continue
                pointer=(24,22,23)[dy];skip=f'fast_tap_done{index}'
                if dx:
                    if abs(dx)==1:a.alu(28,14,15,0 if dx>0 else 2)
                    else:imm(0x2e,28,15,1);a.alu(28,14,28,0 if dx>0 else 2)
                    a.compare(28,4,3);a.branch(4,skip)
                    if abs(dx)==1:a.alu(28,pointer,26,0 if dx>0 else 2)
                    else:imm(0x2e,28,26,1);a.alu(28,pointer,28,0 if dx>0 else 2)
                    pointer=28
                imm(0x21,27,pointer,0);a.alu(27,27,weighted[weight]);a.store(27,pointer)
                a.label(skip)
            if dy:a.label(f'fast_row_done{dy}')
    for dy in (() if optimized else sorted({tap[1] for tap in taps})):
        if dy:
            imm(0x27,23,12,dy); a.compare(23,5,3); a.branch(4,f'tap_row_done{dy}')
        for index,(dx,tap_y,weight) in enumerate(taps):
            if tap_y!=dy: continue
            skip=f'tap_done{index}'
            if dx:
                if dx in (1,-1):
                    a.alu(22,14,15,0 if dx==1 else 2)
                else:
                    imm(0x2e,22,15,1); a.alu(22,14,22,0 if dx==2 else 2)
                a.compare(22,4,3); a.branch(4,skip)  # also catches negative x
            else: imm(0x2a,22,14,0)  # current x is already known in bounds
            imm(0x2e,24,22,2); a.alu(24,(17,18,19)[dy],24)
            imm(0x21,27,24,0); a.alu(27,27,weighted[weight]); a.store(27,24)
            a.label(skip)
        if dy: a.label(f'tap_row_done{dy}')
    a.alu(14,14,15); imm(0x27,13,13,1)
    a.compare(13,4,4); a.branch(4,'pixel')
    # Retire/clear current row, then rotate pointers without modulo division.
    imm(0x2a,24,17,0); a.alu(25,17,29)
    a.label('clear_row'); a.store(0,24); imm(0x27,24,24,4)
    a.compare(24,25,4); a.branch(4,'clear_row')
    imm(0x2a,28,17,0); imm(0x2a,17,18,0); imm(0x2a,18,19,0); imm(0x2a,19,28,0)
    a.alu(16,16,4); imm(0x27,12,12,1)
    if sliced:
        imm(0x21,28,1,112); a.compare(12,28,4)
    else: a.compare(12,5,4)
    a.branch(4,'row')
    imm(0x27,11,0,0); a.branch(0,'restore')
    a.label('reject'); imm(0x27,11,0,8)
    a.label('restore')
    for i,r in enumerate(saved): imm(0x21,r,1,i*4)
    imm(0x27,1,1,frame); a.emit(0x44004800)
    code=a.finish()
    if len(code)>MAX_CODE: raise ValueError('Kernel slot overflow')
    return code
