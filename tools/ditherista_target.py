"""Bounded, no-call, no-jitter OpenRISC kernels for the Ditherista modes."""
from live_preview_patch import Code

MAGIC=0x27c00
ONE_D=0x28000
LINEAR=0x28400
MAGIC_TABLE=0x48c0

def kernel(base,one_d=False,max_dimension=672,allocation_size=0x28800):
    """r3=Y,r4=width,r5=height; r11=status; other registers preserved.

    Caller owns pixels, serializes access, and maintains caches. No scratch.
    Dimensions 1..max_dimension; excludes this entire patch allocation and stack.
    """
    if (not 1<=max_dimension<=1024 or not 0x28800<=allocation_size<=0x80000 or
        not 0x02090000<=base<=0x02200000-allocation_size):
        raise ValueError('unsupported allocation/dimensions')
    a=Code(); imm=a.immediate
    saved=(2,3,4,5,6,7,8,12,13,14,15,16,17,18)
    frame=len(saved)*4
    imm(0x27,1,1,-frame)
    for i,r in enumerate(saved): a.store(r,1,i*4)
    for r in (4,5):
        imm(0x2f,4,r,1); a.branch(4,'reject')
        imm(0x2f,2,r,max_dimension); a.branch(4,'reject')
    a.const(12,0x02090000); a.compare(3,12,4); a.branch(4,'reject')
    a.const(12,0x02200000); a.compare(3,12,3); a.branch(4,'reject')
    a.alu(13,4,5,0x306); a.alu(13,3,13)
    a.compare(13,12,2); a.branch(4,'reject')
    a.compare(13,1,2); a.branch(4,'reject')
    a.const(12,base+allocation_size); a.compare(3,12,3); a.branch(4,'disjoint')
    a.const(12,base); a.compare(13,12,2); a.branch(4,'reject')
    a.label('disjoint'); a.const(6,base+(LINEAR if one_d else MAGIC_TABLE))
    imm(0x27,7,0,0)
    if one_d:
        a.const(17,1<<23); a.const(18,1<<24)
    a.label('row'); imm(0x27,8,0,0)
    if one_d:
        imm(0x27,14,0,0); imm(0x27,15,0,1); imm(0x29,12,7,1)
        imm(0x2f,0,12,0); a.branch(4,'forward')
        imm(0x27,15,0,-1); imm(0x27,8,4,-1)
        a.label('forward'); imm(0x27,16,0,0)
    else:
        imm(0x29,14,7,3); imm(0x2e,14,14,2); a.alu(14,14,6)
    a.label('pixel'); a.alu(2,3,8); imm(0x23,12,2,0)
    if one_d:
        imm(0x2e,12,12,2); a.alu(12,6,12); imm(0x21,12,12,0)
        a.alu(14,14,12); a.compare(14,17,10)
        imm(0x27,13,0,0); a.branch(3,'store')
        imm(0x27,13,0,255); a.alu(14,14,18,2)
    else:
        imm(0x29,13,8,3); a.alu(13,14,13); imm(0x23,13,13,0)
        a.compare(12,13,3); imm(0x27,13,0,0); a.branch(3,'store')
        imm(0x27,13,0,255)
    a.label('store'); a.store(13,2,op=0x36)
    if one_d:
        a.alu(8,8,15); imm(0x27,16,16,1); a.compare(16,4,4)
    else:
        imm(0x27,8,8,1); a.compare(8,4,4)
    a.branch(4,'pixel'); a.alu(3,3,4); imm(0x27,7,7,1)
    a.compare(7,5,4); a.branch(4,'row')
    imm(0x27,11,0,0); a.branch(0,'restore')
    a.label('reject'); imm(0x27,11,0,8)
    a.label('restore')
    for i,r in enumerate(saved): imm(0x21,r,1,i*4)
    imm(0x27,1,1,frame); a.emit(0x44004800)
    code=a.finish(); assert len(code)<=0x400
    return code
