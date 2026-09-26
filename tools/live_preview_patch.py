"""RAM-only FIRST preview prototype, not complete print-only firmware.

Processes only the two observed 320x240 camera allocations at the photo loop's
submission call. Stock submission/rotation and all print code remain intact.
Initial modes: Bayer8, Bayer4, threshold. No labels or printing integration yet.
"""
import struct
from or1k_subset import Assembler
from or1k_subset import branch
from ui_trace_patch import BIAS,build_filtered_dispatch
BAYER = (0,8,2,10,12,4,14,6,3,11,1,9,15,7,13,5)   # 4x4 Bayer ordered-dither matrix

START=0x4200
TABLES=0x4800
CONTROLS=(0x4900,0x4a80,0x4c00)
STATE=0x4e00  # enabled, mode, processed frames, last descriptor (u32 each)
END=0x5000
CALLSITE=0x949c
SUBMIT=0x361c8
DESCRIPTORS=((0x0208a640,0x02099e00),(0x0208a674,0x020b6000))
PIXELS=320*240
FRAME_BYTES=PIXELS*3//2


def bayer8():
    """8x8 Bayer index matrix (moved here from usb_print_calibration, update 32)."""
    matrix=[[0]]
    for _ in range(3):
        n=len(matrix)
        matrix=[[4*matrix[y%n][x%n]+((0,2),(3,1))[y//n][x//n]
                 for x in range(n*2)] for y in range(n*2)]
    assert sorted(v for row in matrix for v in row)==list(range(64))
    return matrix


class Code(Assembler):
    def const(self,r,value):
        self.immediate(6,r,0,value>>16); self.immediate(0x2a,r,r,value)
    def store(self,r,base,off=0,op=0x35):
        self.emit(op<<26|((off&65535)>>11)<<21|base<<16|r<<11|(off&2047))
    def compare(self,a,b,condition=1): self.emit(0x39<<26|condition<<21|a<<16|b<<11)
    def call(self,origin,target):
        self.emit(struct.unpack('<I',branch(origin+len(self.words)*4,target))[0]|1<<26)


def preview(base,diffusion=(),scratch=None,overlay=None,force_print=False,dynamic_frames=False,allocation_size=None,busy_word=None,pre=None,pre_skip=None,normal=None,reject_flush=False):
    if dynamic_frames and (allocation_size is None or allocation_size<=0):
        raise ValueError('Dynamic frames require a protected payload extent')
    a=Code(); imm=a.immediate
    saved=(3,4,5,6,7,8,9,11,12,13,15)
    imm(0x27,1,1,-48)
    for i,r in enumerate(saved): a.store(r,1,i*4)
    if busy_word is not None:
        a.const(11,busy_word);imm(0x21,12,11,0);imm(0x2f,1,12,0);a.branch(4,'restore')
    a.const(11,base+STATE); imm(0x21,12,11,0)
    imm(0x2f,2 if overlay is not None else 1,12,1); a.branch(4,'restore')
    imm(0x21,12,11,4); imm(0x2f,2,12,2+len(diffusion)); a.branch(4,'restore')
    if diffusion:
        if scratch is None: raise ValueError('Diffusion requires private preview scratch')
        # Snapshot mode before any external calls. Table resolution happens later.
    else:
        imm(0x2e,12,12,6); a.const(13,base+TABLES); a.alu(12,12,13)
    a.store(12,1,44)
    # Descriptor objects are fixed firmware globals. Legacy RAM builds also
    # bind pixel addresses; cold-boot builds validate their heap owners instead.
    a.const(11,DESCRIPTORS[0][0]); a.compare(3,11,0); a.branch(4,'first')
    a.const(11,DESCRIPTORS[1][0]); a.compare(3,11); a.branch(4,'restore')
    if not dynamic_frames:a.const(12,DESCRIPTORS[1][1])
    a.branch(0,'descriptor')
    a.label('first')
    if not dynamic_frames:a.const(12,DESCRIPTORS[0][1])
    a.label('descriptor')
    if dynamic_frames:
        imm(0x21,12,3,4)
        a.const(13,0x02090000);a.compare(12,13,4);a.branch(4,'restore')
        a.const(13,0x021ff000-FRAME_BYTES);a.compare(12,13,2);a.branch(4,'restore')
        imm(0x29,13,12,31);imm(0x2f,1,13,0);a.branch(4,'restore')
        a.const(13,base+allocation_size);a.compare(12,13,3);a.branch(4,'frame_disjoint')
        a.const(13,FRAME_BYTES);a.alu(13,12,13);a.const(11,base)
        a.compare(13,11,2);a.branch(4,'restore')
        a.label('frame_disjoint')
        # Accept only the exact start of an allocated FRAME_BYTES heap block.
        # The fixed node pool and 64-step cap bound malformed/cyclic lists.
        a.const(4,0x02087c04);imm(0x21,4,4,0);imm(0x27,5,0,64)
        a.label('frame_owner')
        a.const(6,0x02087c0c);a.compare(4,6,4);a.branch(4,'restore')
        a.const(6,0x0208800c);a.compare(4,6,3);a.branch(4,'restore')
        imm(0x29,6,4,15);imm(0x2f,1,6,12);a.branch(4,'restore')
        imm(0x21,6,4,0);imm(0x2f,1,6,1);a.branch(4,'next_owner')
        imm(0x21,6,4,4);a.compare(6,12);a.branch(4,'next_owner')
        imm(0x21,6,4,8);a.const(7,FRAME_BYTES);a.compare(6,7);a.branch(4,'restore')
        a.branch(0,'frame_owned')
        a.label('next_owner');imm(0x21,4,4,12);imm(0x27,5,5,-1)
        imm(0x2f,1,5,0);a.branch(4,'frame_owner');a.branch(0,'restore')
        a.label('frame_owned')
    else:
        imm(0x21,11,3,4); a.compare(11,12); a.branch(4,'restore')
    a.const(13,PIXELS); a.alu(12,12,13)
    imm(0x21,11,3,8); a.compare(11,12); a.branch(4,'restore')
    for off,size,value in ((24,4,FRAME_BYTES),(32,2,320),(34,2,240),(44,2,320),(46,1,4)):
        imm({4:0x21,2:0x25,1:0x23}[size],11,3,off)
        a.const(12,value); a.compare(11,12); a.branch(4,'restore')
    if force_print:
        # Enforce camera print policy before capture can inspect stock state.
        # This byte is separate from our grayscale/dither selection.
        a.const(12,0x02089342);imm(0x27,13,0,1);a.store(13,12,op=0x36)
    # Refresh CPU's view of a DMA-produced frame before reading its pixels.
    imm(0x21,3,3,4); a.const(4,FRAME_BYTES)
    a.call(base+START,BIAS+0x2980c)
    if overlay is not None:
        a.const(11,base+STATE); imm(0x21,12,11,0)
        imm(0x2f,0,12,0); a.branch(4,'overlay' if normal is None else 'normal')
    if pre is not None:
        if pre_skip is not None:
            # Update 19: sliced modes only read the frame when the worker takes a
            # new snapshot (next row 0, or a mode change); skip the conversion
            # on the other callbacks of the cycle.
            meta,sliced=pre_skip
            imm(0x21,12,1,44)
            for m in sliced:
                imm(0x2f,0,12,m); a.branch(4,'pre_sliced')
            a.branch(0,'pre_run')
            a.label('pre_sliced')
            a.const(13,meta); imm(0x21,11,13,0); a.compare(11,12,1); a.branch(4,'pre_run')
            imm(0x21,11,13,4); imm(0x2f,0,11,0); a.branch(4,'pre_run')
            a.branch(0,'pre_done')
            a.label('pre_run')
        # Dither modes only: convert Y using the frame's own chroma first.
        imm(0x21,3,1,0); imm(0x21,3,3,4); imm(0x27,4,0,320); imm(0x27,5,0,240)
        a.call(base+START,pre)
        if pre_skip is not None: a.label('pre_done')
    imm(0x21,3,1,0); imm(0x21,4,3,8); imm(0x21,3,3,4)
    imm(0x21,5,1,44); imm(0x27,6,0,0)
    if diffusion:
        imm(0x2f,3,5,3); a.branch(4,'diffusion')
        imm(0x2e,5,5,6); a.const(13,base+TABLES); a.alu(5,5,13)
    a.label('row')
    imm(0x29,8,6,7); imm(0x2e,8,8,3); a.alu(8,5,8)
    imm(0x27,7,0,0)
    a.label('pixel')
    imm(0x29,11,7,7); a.alu(11,8,11); imm(0x23,11,11,0)
    imm(0x23,12,3,0); a.compare(12,11,3)
    imm(0x27,12,0,0); a.branch(3,'store')
    imm(0x27,12,0,255)
    a.label('store'); a.store(12,3,op=0x36)
    imm(0x27,3,3,1); imm(0x27,7,7,1)
    imm(0x2f,4,7,320); a.branch(4,'pixel')
    imm(0x27,6,6,1); imm(0x2f,4,6,240); a.branch(4,'row')
    if diffusion:
        a.branch(0,'neutralize')
        a.label('diffusion'); imm(0x2a,12,5,0)
        imm(0x27,4,0,320); imm(0x27,5,0,240); a.const(6,scratch)
        for i,target in enumerate(diffusion):
            imm(0x2f,1,12,3+i); a.branch(4,f'next_diffusion{i}')
            a.call(base+START,target); a.branch(0,'diffusion_done')
            a.label(f'next_diffusion{i}')
        a.branch(0,'restore')
        a.label('diffusion_done')
        if not reject_flush: imm(0x2f,1,11,0); a.branch(4,'restore')
        # Update 32 (reject_flush): a rejecting effect still gets neutral chroma and
        # the flush, so the frame shown is the already converted gray, never stale lines.
        imm(0x21,3,1,0); imm(0x21,4,3,8)
        a.label('neutralize')
    # Neutral chroma for monochrome preview, preserving descriptor and padding.
    a.const(11,0x80808080); a.const(12,PIXELS//8)
    a.label('chroma'); a.store(11,4)
    imm(0x27,4,4,4); imm(0x27,12,12,-1)
    imm(0x2f,1,12,0); a.branch(4,'chroma')
    if normal is not None:
        # Update 24: normal (dither off) frames become the print gray + filter curve.
        a.branch(0,'overlay')
        a.label('normal'); imm(0x21,3,1,0); imm(0x21,3,3,4)
        a.call(base+START,normal)
    if overlay is not None:
        a.label('overlay')
        imm(0x21,3,1,0); imm(0x21,3,3,4); a.const(11,base+STATE)
        imm(0x21,4,11,0); imm(0x21,5,11,4)
        a.call(base+START,overlay)
    imm(0x21,3,1,0); imm(0x21,3,3,4); a.const(4,FRAME_BYTES)
    a.call(base+START,BIAS+0x29790)  # publish edits before stock hardware submission
    a.const(11,base+STATE); imm(0x21,12,11,8); imm(0x27,12,12,1); a.store(12,11,8)
    imm(0x21,12,1,0); a.store(12,11,12)
    a.label('restore')
    for i,r in enumerate(saved): imm(0x21,r,1,i*4)
    imm(0x27,1,1,48)
    code=a.finish()
    code+=branch(base+START+len(code),BIAS+SUBMIT)
    if len(code)>TABLES-START: raise ValueError('Preview code overflow')
    return code


def control(base,index,mode_count=3,reset_word=None,excluded_modes=()):
    if any(not 0<m<mode_count-1 for m in excluded_modes):raise ValueError('excluded modes must be interior slots')
    a=Code(); imm=a.immediate
    saved=(12,13,15)
    imm(0x27,1,1,-12)
    for i,r in enumerate(saved): a.store(r,1,i*4)
    a.const(15,base+STATE)
    if index:
        imm(0x21,12,15,0); imm(0x2f,1,12,1); a.branch(4,'stock')
    # Only the observed press subtype (0), not release (2) or repeats.
    imm(0x2f,1,4,1); a.branch(4,'done')
    a.const(12,0x02000000); a.compare(5,12,4); a.branch(4,'done')
    a.const(12,0x021ffffc); a.compare(5,12,2); a.branch(4,'done')
    imm(0x29,12,5,3); imm(0x2f,1,12,0); a.branch(4,'done')
    imm(0x21,12,5,0); imm(0x2f,1,12,0); a.branch(4,'done')
    if reset_word is not None:
        a.const(13,reset_word); a.store(0,13)
    if index==0:
        imm(0x21,12,15,0); imm(0x2b,12,12,1); imm(0x29,12,12,1); a.store(12,15,0)
        # This replaces, rather than calls, the original print/save toggle.
        # It does NOT globally enforce print-only operation in other screens.
    else:
        imm(0x21,12,15,4)
        if index==1:
            imm(0x2f,1,12,0); a.branch(4,'decrement')
            imm(0x27,12,0,mode_count)
            a.label('decrement'); imm(0x27,12,12,-1)
        else:
            imm(0x27,12,12,1); imm(0x2f,4,12,mode_count); a.branch(4,'mode')
            imm(0x27,12,0,0)
        a.label('mode')
        for m in sorted(set(excluded_modes),reverse=index==1):
            imm(0x2f,1,12,m);a.branch(4,f'keep_mode{m}')
            imm(0x27,12,12,-1 if index==1 else 1);a.label(f'keep_mode{m}')
        a.store(12,15,4)
    a.label('done')
    for i,r in enumerate(saved): imm(0x21,r,1,i*4)
    imm(0x27,1,1,12); imm(0x27,11,0,0); a.emit(0x44004800)
    if index:
        a.label('stock')
        for i,r in enumerate(saved): imm(0x21,r,1,i*4)
        imm(0x27,1,1,12)
        a.emit(struct.unpack('<I',branch(base+CONTROLS[index]+len(a.words)*4,
                                       BIAS+(0xc1d4 if index==1 else 0xbef4)))[0])
    code=a.finish()
    if len(code)>0x180: raise ValueError('Control slot overflow')
    return code


def build(base,original):
    previous,patches=build_filtered_dispatch(base,original)
    blob=bytearray(previous)
    code=preview(base); blob[START:START+len(code)]=code
    matrix=bayer8()
    thresholds=bytes(((2*matrix[y][x]+1)*255)//128+1 for y in range(8) for x in range(8))
    thresholds+=bytes(8+16*BAYER[(y%4)*4+x%4] for y in range(8) for x in range(8))
    thresholds+=bytes([128]*64)
    blob[TABLES:TABLES+192]=thresholds
    changes=[]
    for index,slot in enumerate(CONTROLS):
        code=control(base,index); blob[slot:slot+len(code)]=code
        patch=dict(patches[index]); patch['original']=patch['replacement']
        patch['replacement']=struct.pack('<I',base+slot).hex()
        patch['label']='preview '+('toggle','previous','next')[index]
        changes.append(patch)
    expected=struct.unpack('<I',branch(BIAS+CALLSITE,BIAS+SUBMIT))[0]|1<<26
    if original[CALLSITE:CALLSITE+4]!=struct.pack('<I',expected): raise ValueError('Unexpected submission call')
    replacement=struct.unpack('<I',branch(BIAS+CALLSITE,base+START))[0]|1<<26
    changes.append({'address':BIAS+CALLSITE,'original':struct.pack('<I',expected).hex(),
                    'replacement':struct.pack('<I',replacement).hex(),'label':'photo frame submission'})
    return bytes(blob),changes
