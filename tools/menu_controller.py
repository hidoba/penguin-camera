"""Native stock-menu adapters; print requests are queued, not executed yet.

Uses the stock menu's landscape descriptor pool and mode dispatcher. No flash-writing,
printer, or capture/save calls. Random penguin request needs a validated worker.
"""
import struct
from live_preview_patch import Code,BIAS
from build_gray_candidate import branch
from menu_target import DRAW,SIZE,protected_range

DISPLAY=0x2c800
PREVIOUS=0x2cc00
NEXT=0x2cf00
ACTIVATE=0x2d200
STATUS=0x2d600  # last draw status, draw count, pending random-print request
SELECTION=0x02089334
LCD=(0x0208a6e4,0x0208a718)
LCD_BYTES=122880
LCD_Y=81920
MENU=(0x0208a640,0x0208a674)
FRAME_Y=320*240
FRAME_BYTES=FRAME_Y*3//2

def prologue(a):
    saved=tuple(r for r in range(2,31) if r!=11)
    a.immediate(0x27,1,1,-112)
    for i,r in enumerate(saved):a.store(r,1,i*4)
    return saved

def epilogue(a,saved):
    for i,r in enumerate(saved):a.immediate(0x21,r,1,i*4)
    a.immediate(0x27,1,1,112);a.emit(0x44004800)

def display(base,protected=None,diagnostic=False,allocation_size=SIZE,after_draw=None):
    protect_start,protect_end=protected_range(base,protected,allocation_size)
    a=Code();imm=a.immediate;saved=prologue(a);origin=base+DISPLAY
    def stage(value):
        if diagnostic:
            a.const(12,base+STATUS);imm(0x27,13,0,value);a.store(13,12,16)
    def snapshot(label):
        if diagnostic:
            a.const(12,base+STATUS+32);imm(0x2a,13,18,0);imm(0x27,15,0,13)
            a.label(label);imm(0x21,16,13,0);a.store(16,12)
            imm(0x27,12,12,4);imm(0x27,13,13,4);imm(0x27,15,15,-1)
            imm(0x2f,1,15,0);a.branch(4,label)
    if diagnostic:
        a.const(12,base+STATUS);imm(0x21,13,12,12);imm(0x27,13,13,1);a.store(13,12,12)
    stage(1)
    imm(0x27,3,0,0);a.call(origin,BIAS+0x609c)  # same getter as original menu
    if diagnostic:
        a.const(12,base+STATUS);a.store(11,12,20)
    imm(0x2a,18,11,0);a.const(12,MENU[0]);a.compare(18,12,0);a.branch(4,'known')
    a.const(12,MENU[1]);a.compare(18,12);a.branch(4,'other_pool')
    a.label('known')
    stage(2);snapshot('snapshot_before')
    # On-device stock trace: claimed pool-0 format-4, linear 320x240 YUV.
    # Plane bases are +4/+8; +12/+16 are unused (zero), NOT allocation bases.
    for off,value in ((46,4),(49,1),(50,0)):
        imm(0x23,12,18,off);imm(0x2f,1,12,value);a.branch(4,'release_reject')
    stage(3)
    imm(0x21,20,18,4)
    a.const(12,0x02090000);a.compare(20,12,4);a.branch(4,'release_reject')
    a.const(12,0x02200000-FRAME_BYTES);a.compare(20,12,2);a.branch(4,'release_reject')
    imm(0x29,12,20,31);imm(0x2f,1,12,0);a.branch(4,'release_reject')
    a.const(12,FRAME_Y);a.alu(12,20,12);imm(0x21,13,18,8)
    a.compare(12,13);a.branch(4,'release_reject')
    a.const(12,FRAME_BYTES);a.alu(13,20,12);a.compare(13,1,2);a.branch(4,'release_reject')
    a.const(12,protect_end);a.compare(20,12,3);a.branch(4,'disjoint')
    a.const(12,protect_start);a.compare(13,12,2);a.branch(4,'release_reject')
    a.label('disjoint')
    stage(4)
    # Stock setup/submission handles rotation later. We draw linear landscape.
    imm(0x2a,3,18,0);imm(0x27,4,0,0);imm(0x27,5,0,0)
    imm(0x27,6,0,320);imm(0x27,7,0,240);a.call(origin,BIAS+0x405dc)
    stage(5);snapshot('snapshot_after')
    imm(0x21,12,18,4);a.compare(12,20);a.branch(4,'release_reject')
    a.const(13,FRAME_Y);a.alu(13,20,13);imm(0x21,12,18,8)
    a.compare(12,13);a.branch(4,'release_reject')
    for off,size,value in ((24,4,FRAME_BYTES),(32,2,320),(34,2,240),(44,2,320)):
        imm(0x21 if size==4 else 0x25,12,18,off);a.const(13,value)
        a.compare(12,13);a.branch(4,'release_reject')
    stage(6)
    a.const(12,SELECTION);imm(0x23,4,12,0);imm(0x2f,5,4,2);a.branch(4,'selection_ok')
    imm(0x27,4,0,0);a.store(4,12,op=0x36)
    a.label('selection_ok');imm(0x2a,3,20,0);a.call(origin,base+DRAW)
    imm(0x2f,1,11,0);a.branch(4,'release_reject')
    if after_draw is not None:
        imm(0x2a,3,20,0);a.call(origin,after_draw)  # update 31: menu penguin (keeps registers)
    a.const(12,FRAME_Y);a.alu(12,20,12);a.const(13,0x80808080);imm(0x27,15,0,(FRAME_BYTES-FRAME_Y)//4)
    a.label('chroma');a.store(13,12);imm(0x27,12,12,4);imm(0x27,15,15,-1)
    imm(0x2f,1,15,0);a.branch(4,'chroma')
    imm(0x2a,3,20,0);a.const(4,FRAME_BYTES);a.call(origin,BIAS+0x29790)
    imm(0x27,3,0,0);imm(0x2a,4,18,0);imm(0x27,5,0,0);imm(0x27,6,0,0)
    imm(0x27,7,0,255);a.call(origin,BIAS+0x6058)  # stock submission consumes descriptor
    a.const(12,base+STATUS);imm(0x21,13,12,4);imm(0x27,13,13,1);a.store(13,12,4)
    imm(0x27,11,0,0);a.branch(0,'done')
    # Do not leak a known LCD descriptor if called in a different display mode.
    a.label('other_pool');a.const(12,LCD[0]);a.compare(18,12,0);a.branch(4,'release_reject')
    a.const(12,LCD[1]);a.compare(18,12);a.branch(4,'reject')
    a.label('release_reject');imm(0x2a,3,18,0);a.call(origin,BIAS+0x408f4)
    a.label('reject');imm(0x27,11,0,8)
    a.label('done');a.const(12,base+STATUS);a.store(11,12);epilogue(a,saved)
    code=a.finish()
    if len(code)>PREVIOUS-DISPLAY:raise ValueError('display adapter overflow')
    return code

def event(base,index):
    """index 0=previous, 1=next, 2=activate. Consume only payload-0 presses."""
    off=(PREVIOUS,NEXT,ACTIVATE)[index];origin=base+off
    a=Code();imm=a.immediate;saved=prologue(a)
    imm(0x2f,1,4,1);a.branch(4,'done')
    a.const(12,0x02000000);a.compare(5,12,4);a.branch(4,'done')
    a.const(12,0x021ffffc);a.compare(5,12,2);a.branch(4,'done')
    imm(0x29,12,5,3);imm(0x2f,1,12,0);a.branch(4,'done')
    imm(0x21,12,5,0);imm(0x2f,1,12,0);a.branch(4,'done')
    a.const(18,SELECTION);imm(0x23,20,18,0)
    imm(0x2f,5,20,2);a.branch(4,'valid');imm(0x27,20,0,0)
    a.label('valid')
    if index<2:
        if index==0:
            imm(0x2f,1,20,0);a.branch(4,'decrement');imm(0x27,20,0,3)
            a.label('decrement');imm(0x27,20,20,-1)
        else:
            imm(0x27,20,20,1);imm(0x2f,4,20,3);a.branch(4,'store');imm(0x27,20,0,0)
        a.label('store');a.store(20,18,op=0x36);a.call(origin,base+DISPLAY)
    else:
        imm(0x2f,1,20,2);a.branch(4,'enter_mode')
        # Latched request, never a jump into an unvalidated print routine.
        a.const(12,base+STATUS);imm(0x27,13,0,1);a.store(13,12,8);a.branch(0,'done')
        a.label('enter_mode');a.call(origin,BIAS+0x17cf8)  # original hidden menu widgets
        imm(0x27,3,0,1);a.call(origin,BIAS+0x4e298)
        imm(0x27,3,0,3);imm(0x2f,0,20,0);a.branch(4,'request')
        imm(0x27,3,0,8)
        a.label('request');imm(0x27,4,0,0);a.call(origin,BIAS+0x908c)
    a.label('done');imm(0x27,11,0,0);epilogue(a,saved)
    code=a.finish()
    if len(code)>0x300:raise ValueError('menu event adapter overflow')
    return code

def install_parts(base,original,blob):
    parts=[(DISPLAY,display(base))]+[(off,event(base,i)) for i,off in enumerate((PREVIOUS,NEXT,ACTIVATE))]
    for off,data in parts:blob[off:off+len(data)]=data
    patches=[]
    entries=[(0x17940,0xd7e117ec,branch(BIAS+0x17940,base+DISPLAY),'native menu display'),
             (0x17de8,0xbca40005,struct.pack('<I',0xbca40002),'three-entry menu init')]
    for off,old,target in ((0x826cc,BIAS+0x17c6c,PREVIOUS),(0x826dc,BIAS+0x17c6c,PREVIOUS),
                           (0x826d4,BIAS+0x17be4,NEXT),(0x826e4,BIAS+0x17f10,ACTIVATE)):
        entries.append((off,old,struct.pack('<I',base+target),'native menu event'))
    for off,old,new,label in entries:
        expected=struct.pack('<I',old)
        if original[off:off+4]!=expected:raise ValueError(f'wrong menu instruction at {off:#x}')
        patches.append(dict(address=BIAS+off,original=expected.hex(),replacement=new.hex(),label=label))
    return patches
