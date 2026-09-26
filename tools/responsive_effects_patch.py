"""Nine-mode prototype with cooperative, full-resolution diffusion preview.

Each preview callback does at most 16/32 diffusion rows and returns to stock
dispatch. A private snapshot and two bit-packed result banks prevent tearing
or showing half-processed frames. Printing remains full-frame/full-resolution.
"""
import struct
from extended_effects_patch import build as old_build, PREVIEW_SCRATCH, PRINT_SCRATCH
from combined_print_patch import transform, TRANSFORM, LUT
from live_preview_patch import Code, preview, control, START, TABLES, CONTROLS
from or1k_subset import branch
from diffusion_target import make_kernel
from preview_labels import overlay, font_data, label_data, LABEL_CODE, FONT, LABELS
from ditherista_target import kernel as fast_kernel, HALFTONE4, ONE_D, LINEAR, HALFTONE4_TABLE
from ditherista_tables import THRESHOLDS, LINEAR_Q24

SIZE=0x28800
# User explicitly requested visual review before any deployment. Both cracked
# diffusion candidates were rejected; keep the hardware installer closed until
# the actual Ditherboy-style effect is selected and deployment is requested.
KERNELS=(0x5000,0x6000,0x7000,0xb000)
SLICED=(0x1100,0x2100,0x3100,0xc000)
WORKER=0xd000
EXPAND_LUT=0xe000
ADAPTERS=(0xe800,0xe820,0xe840,0xe860)
BANKS=(0xf000,0x12000)
WORK_IMAGE=0x15000
META=0x4e40  # mode, next row, active bank, valid, completed, chunks, last status
NAMES=('Bayer 8x8','Bayer 4x4','Threshold','Floyd–Steinberg','Atkinson','Stucki','Cracked diffusion','Halftone 4x4','Error Diffusion 1D')
MODES=('floyd','atkinson','stucki','cracked')


def worker(base,rows=32,stucki_rows=16,*,packed_words=False,rows_by_mode=None,extra=None):
    """r3=current 320x240 Y, r7=mode 3..6; preserves all except r11=status.

    Called only after the outer wrapper validates the exact source descriptor
    and invalidates the DMA frame. Scratch is owned by this fresh allocation.
    """
    if any(n%16 or not 16<=n<=128 for n in (rows,stucki_rows)):raise ValueError('Invalid preview slice budget')
    a=Code(); imm=a.immediate
    saved=tuple(r for r in range(2,31) if r!=11)
    imm(0x27,1,1,-112)
    for i,r in enumerate(saved): a.store(r,1,i*4)
    imm(0x2f,4,7,3); a.branch(4,'reject')
    if extra:
        # Update 19: extra sliced modes (e.g. 10 = halftone) beyond 3..6.
        imm(0x2f,5,7,6); a.branch(4,'mode_ok')
        for m in sorted(extra):
            imm(0x2f,0,7,m); a.branch(4,'mode_ok')
        a.branch(0,'reject'); a.label('mode_ok')
    else:
        imm(0x2f,2,7,6); a.branch(4,'reject')
    imm(0x2f,1,4,320); a.branch(4,'reject')
    imm(0x2f,1,5,240); a.branch(4,'reject')
    imm(0x2a,2,3,0); imm(0x2a,14,7,0); a.const(18,base+META)
    imm(0x21,12,18,0); a.compare(12,14,0); a.branch(4,'same_mode')
    a.store(14,18,0); a.store(0,18,4); a.store(0,18,8); a.store(0,18,12)
    a.label('same_mode'); imm(0x21,20,18,4)
    imm(0x2f,3,20,240); a.branch(4,'reject')
    imm(0x29,12,20,15); imm(0x2f,1,12,0); a.branch(4,'reject')
    for off in (8,12):
        imm(0x21,12,18,off); imm(0x2f,2,12,1); a.branch(4,'reject')
    imm(0x2f,1,20,0); a.branch(4,'snapshot_ready')
    # One owned source frame per completed result; future sensor frames cannot
    # change the remaining input or error propagation between slices.
    imm(0x2a,12,2,0); a.const(13,base+WORK_IMAGE); imm(0x27,15,0,19200)
    a.label('snapshot'); imm(0x21,11,12,0); a.store(11,13)
    imm(0x27,12,12,4); imm(0x27,13,13,4); imm(0x27,15,15,-1)
    imm(0x2f,1,15,0); a.branch(4,'snapshot')
    a.label('snapshot_ready')
    imm(0x27,22,0,rows); imm(0x2f,1,14,5); a.branch(4,'rows_chosen')
    imm(0x27,22,0,stucki_rows)  # Stucki has more taps
    a.label('rows_chosen')
    for m,n in sorted((rows_by_mode or {}).items()):
        if n%16 or not 16<=n<=128:raise ValueError('Invalid preview slice budget')
        imm(0x2f,1,14,m); a.branch(4,f'rows_mode{m}'); imm(0x27,22,0,n); a.label(f'rows_mode{m}')
    imm(0x27,12,0,240); a.alu(12,12,20,2)
    a.compare(22,12,5); a.branch(4,'rows_clamped'); imm(0x2a,22,12,0)
    a.label('rows_clamped')
    a.const(3,base+WORK_IMAGE); imm(0x27,4,0,320); imm(0x27,5,0,240)
    a.const(6,base+PREVIEW_SCRATCH); imm(0x2a,7,20,0); imm(0x2a,8,22,0)
    for i,off in enumerate(SLICED):
        imm(0x2f,1,14,3+i); a.branch(4,f'next_kernel{i}')
        a.call(base+WORKER,base+off); a.branch(0,'rendered')
        a.label(f'next_kernel{i}')
    for m,target in sorted((extra or {}).items()):
        imm(0x2f,1,14,m); a.branch(4,f'next_extra{m}')
        a.call(base+WORKER,target); a.branch(0,'rendered')
        a.label(f'next_extra{m}')
    a.branch(0,'reject')
    a.label('rendered'); imm(0x2f,1,11,0); a.branch(4,'reject')
    # Pack just the completed rows into the hidden bank, MSB first.
    imm(0x21,12,18,8); imm(0x2f,0,12,0); a.branch(4,'build_bank1')
    a.const(24,base+BANKS[0]); a.branch(0,'build_bank_ready')
    a.label('build_bank1'); a.const(24,base+BANKS[1])
    a.label('build_bank_ready'); imm(0x27,12,0,40)
    a.alu(13,20,12,0x306); a.alu(24,24,13); a.alu(26,22,12,0x306)
    imm(0x2e,13,13,3); a.const(28,base+WORK_IMAGE); a.alu(28,28,13)
    if packed_words:
        # Eight exact 0/255 pixels, little-endian aligned words. Isolate one
        # bit per byte, then multiply to gather each four-pixel MSB-first nibble.
        a.const(3,0x01010101);a.const(4,0x08040201)
    a.label('pack_byte')
    if packed_words:
        for reg,offset in ((12,0),(13,4)):
            imm(0x21,reg,28,offset);a.alu(reg,reg,3,3)
            a.alu(reg,reg,4,0x306);imm(0x2e,reg,reg,0x58)
        imm(0x2e,12,12,4);a.alu(12,12,13,4);imm(0x27,28,28,8)
    else:
        imm(0x27,12,0,0); imm(0x27,13,0,8)
        a.label('pack_bit'); imm(0x23,11,28,0); imm(0x2e,11,11,0x47)
        imm(0x2e,12,12,1); a.alu(12,12,11,4); imm(0x27,28,28,1)
        imm(0x27,13,13,-1); imm(0x2f,1,13,0); a.branch(4,'pack_bit')
    a.store(12,24,op=0x36); imm(0x27,24,24,1); imm(0x27,26,26,-1)
    imm(0x2f,1,26,0); a.branch(4,'pack_byte')
    a.alu(20,20,22); a.store(20,18,4)
    imm(0x21,12,18,20); imm(0x27,12,12,1); a.store(12,18,20)
    imm(0x2f,1,20,240); a.branch(4,'display')
    a.store(0,18,4); imm(0x21,12,18,8); imm(0x2b,12,12,1); a.store(12,18,8)
    imm(0x27,12,0,1); a.store(12,18,12)
    imm(0x21,12,18,16); imm(0x27,12,12,1); a.store(12,18,16)
    a.label('display'); imm(0x21,12,18,12); imm(0x2f,0,12,0); a.branch(4,'success')
    imm(0x21,12,18,8); imm(0x2f,0,12,0); a.branch(4,'display_bank0')
    a.const(24,base+BANKS[1]); a.branch(0,'display_bank_ready')
    a.label('display_bank0'); a.const(24,base+BANKS[0])
    a.label('display_bank_ready'); a.const(26,base+EXPAND_LUT)
    imm(0x2a,28,2,0); imm(0x27,15,0,9600)
    a.label('expand'); imm(0x23,12,24,0); imm(0x2e,12,12,3); a.alu(12,26,12)
    imm(0x21,11,12,0); imm(0x21,13,12,4); a.store(11,28); a.store(13,28,4)
    imm(0x27,24,24,1); imm(0x27,28,28,8); imm(0x27,15,15,-1)
    imm(0x2f,1,15,0); a.branch(4,'expand')
    a.label('success'); imm(0x27,11,0,0); a.branch(0,'restore')
    a.label('reject'); imm(0x27,11,0,8)
    # Don't display any partially built result after an invalid state.
    a.const(18,base+META); a.store(0,18,0); a.store(0,18,4); a.store(0,18,12)
    a.label('restore'); a.const(18,base+META); a.store(11,18,24)
    for i,r in enumerate(saved): imm(0x21,r,1,i*4)
    imm(0x27,1,1,112); a.emit(0x44004800)
    code=a.finish()
    assert len(code)<LABEL_CODE-WORKER
    return code


def build(base,original):
    if base%64 or not 0x02090000<=base<=0x02200000-SIZE: raise ValueError('Unapproved responsive allocation')
    old,patches=old_build(base,original)
    blob=bytearray(SIZE); blob[:len(old)]=old
    for mode,off,sliced in zip(MODES,KERNELS,SLICED):
        for slot,is_sliced in ((off,False),(sliced,True)):
            code=make_kernel(mode,base+slot,sliced=is_sliced); blob[slot:slot+len(code)]=code
    code=worker(base); blob[WORKER:WORKER+len(code)]=code
    for i,off in enumerate(ADAPTERS):
        a=Code(); a.immediate(0x27,7,0,3+i)
        code=a.finish()+branch(base+off+4,base+WORKER); blob[off:off+len(code)]=code
    code=preview(base,tuple(base+off for off in (*ADAPTERS,HALFTONE4,ONE_D)),base+PREVIEW_SCRATCH,base+LABEL_CODE)
    blob[START:TABLES]=b'\0'*(TABLES-START); blob[START:START+len(code)]=code
    code=transform(base,tuple(base+off for off in (*KERNELS,HALFTONE4,ONE_D)),base+PRINT_SCRATCH,SIZE)
    blob[TRANSFORM:LUT]=b'\0'*(LUT-TRANSFORM); blob[TRANSFORM:TRANSFORM+len(code)]=code
    for i,off in enumerate(CONTROLS):
        code=control(base,i,len(NAMES),base+META)
        blob[off:off+0x180]=b'\0'*0x180; blob[off:off+len(code)]=code
    blob[EXPAND_LUT:EXPAND_LUT+2048]=bytes(255 if value&(1<<bit) else 0 for value in range(256) for bit in range(7,-1,-1))
    for off,code in ((LABEL_CODE,overlay(base)),(FONT,font_data()),(LABELS,label_data())):
        blob[off:off+len(code)]=code
    for off,one_d in ((HALFTONE4,False),(ONE_D,True)):
        code=fast_kernel(base,one_d); blob[off:off+len(code)]=code
    blob[HALFTONE4_TABLE:HALFTONE4_TABLE+16]=THRESHOLDS
    blob[LINEAR:LINEAR+1024]=struct.pack('<256i',*LINEAR_Q24)
    return bytes(blob),patches
