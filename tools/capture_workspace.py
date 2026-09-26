"""Photo-only scratch loan; no new heap allocation and no storage writes.

During synchronous capture the preview callback cannot run. Reuse its 76800
byte snapshot as JPEG workspace, with the same size successfully used by stock
unframed captures. Keep stock raw allocation and peripheral shutdown. Never
pass the borrowed pointer to free(). Unknown/stale state refuses or latches.
"""
import struct
from live_preview_patch import Code,BIAS
from build_gray_candidate import branch
from responsive_effects_patch import WORK_IMAGE,META,BANKS

ALLOC=0x2d800  # verified empty gap after menu status, before penguin reader
CLEANUP=ALLOC+0x400
STATE=0x4e80  # active, raw pointer, attempts, status
WORKSPACE_BYTES=76800
# Update 32: the stock capture re-encodes at lower quality only above 0x14000 bytes,
# so lend a larger contiguous range: preview bank 0 .. end of WORK_IMAGE. Nothing
# else lives there (bank 1 ends 0x14580, gap to 0x15000 unused, Magic at 0x27c00),
# and the preview (the only user) cannot run during the synchronous capture.
LOAN_START=BANKS[0]
LOAN_BYTES=WORK_IMAGE+WORKSPACE_BYTES-BANKS[0]
assert LOAN_BYTES>=0x14000
GLOBALS=0x0208935c


def allocator(base,start=WORK_IMAGE,size=WORKSPACE_BYTES):
    a=Code();imm=a.immediate;origin=base+ALLOC
    saved=tuple(r for r in range(2,31) if r!=11)
    imm(0x27,1,1,-112)
    for i,r in enumerate(saved):a.store(r,1,i*4)
    a.const(18,base+STATE);a.const(20,GLOBALS)
    imm(0x2f,1,3,1);a.branch(4,'reject')
    for r,off in ((18,0),(20,24),(20,56)):
        imm(0x21,12,r,off);imm(0x2f,1,12,0);a.branch(4,'reject')
    for address,expected in ((0x0208a1dc,640),(0x0208a2d4,480)):
        a.const(12,address);imm(0x21,12,12,0);imm(0x2f,1,12,expected);a.branch(4,'reject')
    # Clear snapshot validity; do not display JPEG bytes after returning.
    a.const(12,base+META)
    for off in (0,4,12):a.store(0,12,off)
    imm(0x21,12,18,8);imm(0x27,12,12,1);a.store(12,18,8)
    a.const(3,base+start);a.const(4,size);a.call(origin,BIAS+0x29790)
    a.const(12,base+start);a.store(12,20,56)
    a.const(12,size);a.store(12,20,60)
    imm(0x27,12,0,1);a.store(12,18,0)
    imm(0x27,3,0,1)
    a.call(origin,BIAS+0x3cb24)
    a.store(11,18,12);imm(0x2f,1,11,0);a.branch(4,'failed')
    imm(0x21,12,20,24);a.store(12,18,4);a.branch(0,'done')
    a.label('failed')
    # Stock raw allocation failure leaves raw NULL and has not started DMA.
    # Refuse to clean an unexpected non-NULL raw pointer automatically.
    imm(0x21,12,20,24);imm(0x2f,1,12,0);a.branch(4,'done')
    a.store(0,20,56);a.store(0,20,60);a.store(0,18,0);a.branch(0,'done')
    a.label('reject');imm(0x27,11,0,-2);a.store(11,18,12)
    a.label('done')
    for i,r in enumerate(saved):imm(0x21,r,1,i*4)
    imm(0x27,1,1,112);a.emit(0x44004800)
    code=a.finish()
    if len(code)>0x400:raise ValueError('Capture allocator overflow')
    return code


def cleanup(base,start=WORK_IMAGE,size=WORKSPACE_BYTES):
    a=Code();imm=a.immediate;origin=base+CLEANUP
    saved=tuple(r for r in range(2,31) if r!=11)
    imm(0x27,1,1,-112)
    for i,r in enumerate(saved):a.store(r,1,i*4)
    a.const(18,base+STATE);a.const(20,GLOBALS)
    imm(0x21,12,18,0);imm(0x2f,0,12,0);a.branch(4,'stock')
    imm(0x2f,1,12,1);a.branch(4,'reject')
    imm(0x21,12,20,56);a.const(13,base+start);a.compare(12,13);a.branch(4,'reject')
    imm(0x21,12,20,60);a.const(13,size);a.compare(12,13);a.branch(4,'reject')
    imm(0x21,12,20,24);imm(0x21,13,18,4);a.compare(12,13);a.branch(4,'reject')
    imm(0x2f,0,12,0);a.branch(4,'reject')
    # Exact shutdown sequence from stock 3cd64, before releasing its raw buffer.
    imm(0x27,3,0,3);imm(0x27,4,0,0);a.call(origin,BIAS+0x31618)
    imm(0x27,3,0,0);a.call(origin,BIAS+0x33888)
    imm(0x27,3,0,0);a.call(origin,BIAS+0x337b0)
    a.const(3,base+start);a.const(4,size);a.call(origin,BIAS+0x2980c)
    a.store(0,20,56);a.store(0,20,60)
    a.call(origin,BIAS+0x3cc80)  # stock free raw + clear capture fields; JPEG NULL
    a.store(0,18,0);a.store(0,18,4);a.branch(0,'done')
    a.label('stock');a.call(origin,BIAS+0x3cd64);a.branch(0,'done')
    a.label('reject');imm(0x27,11,0,-2);a.store(11,18,12)
    a.label('done')
    for i,r in enumerate(saved):imm(0x21,r,1,i*4)
    imm(0x27,1,1,112);a.emit(0x44004800)
    code=a.finish()
    if len(code)>0x400:raise ValueError('Capture cleanup overflow')
    return code


def install_parts(base,original,blob):
    patches=[]
    for off,target,slot,make in ((0x9c80,0x3cb24,ALLOC,allocator),(0x9eb4,0x3cd64,CLEANUP,cleanup)):
        if any(blob[slot:slot+0x400]):raise ValueError('Capture slot occupied')
        old=struct.pack('<I',int.from_bytes(branch(BIAS+off,BIAS+target),'little')|1<<26)
        if original[off:off+4]!=old:raise ValueError('Capture call mismatch')
        code=make(base);blob[slot:slot+len(code)]=code
        new=struct.pack('<I',int.from_bytes(branch(BIAS+off,base+slot),'little')|1<<26)
        patches.append({'address':BIAS+off,'original':old.hex(),'replacement':new.hex(),
                        'label':'photo capture workspace '+('loan' if slot==ALLOC else 'cleanup')})
    return patches
