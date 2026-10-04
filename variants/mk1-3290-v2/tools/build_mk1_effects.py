#!/usr/bin/env python3
"""Offline, reproducible MK1 native effects candidate. Never opens USB or writes flash."""
import argparse, hashlib, json, struct
from pathlib import Path
from mk1_native import Code, BIAS, HEAP, RAM_END, MAX_DIM, branch, save, restore, load_args
from penguin_port import color_gray, gray_filters, tone_gray, date_stamp, effects_v5_target, diffusion_target, preview_labels
from penguin_port.effects_v5 import bayer8_thresholds
ROOT=Path(__file__).resolve().parents[1]
STOCK_SHA='33ac2db5716dacf06b60f97c5efbb2b2b77d820fe2b13e58c1eeb4379094fab5'
BASE_SHA='c1dd84025bc7ea79e64bfb1e8f83ca028d63996f071b7e4682e29f380c605ec0'
CAVE=0x785d8; BASE=CAVE+BIAS; SIZE=0x7c3dc-CAVE
G=0x020cee44; OSD=0x020c7c08; CAPTURE=0x020c870c
MALLOC=0x0203d8d0; FREE=0x0203d9a0; FLUSH=0x0202a3bc
REGS=tuple(range(2,32))
NAMES=('BAYER 8X8','HALFTONE 6X6','FLOYD-STEINBERG')

def digest(b): return hashlib.sha256(b).hexdigest()
def ret(a): a.emit(0x44004800)
def move(a,d,s): a.immediate(0x2a,d,s,0)
def begin():
    a=Code(); save(a,REGS); return a

def end(a): restore(a,REGS); ret(a); return a.finish()
def args(a,rs): load_args(a,REGS,rs)
def addr(layout,key): return BASE+layout[key]
def check_planes(a,tag='v'):
    """Require even, finite geometry and separate owned planes in the SDK heap.
    Reject jumps to done; caller has established allocation ownership via stock ABI.
    Uses r12..15; r16=size, r17=size/2. This excludes code, globals, OSD and stack.
    """
    i=a.immediate
    for r,lo in ((4,8),(5,2)):
        i(0x2f,4,r,lo); a.branch(4,'done'); i(0x2f,2,r,MAX_DIM); a.branch(4,'done')
        i(0x29,12,r,1); i(0x2f,1,12,0); a.branch(4,'done')
    a.alu(16,4,5,0x306); i(0x2e,17,16,0x41)
    for r,n in ((3,16),(6,17)):
        a.const(12,HEAP); a.compare(r,12,4); a.branch(4,'done')
        a.alu(13,r,n); a.const(12,RAM_END); a.compare(13,12,2); a.branch(4,'done')
        a.compare(13,r,5); a.branch(4,'done'); a.compare(13,1,2); a.branch(4,'done')
    a.alu(12,3,16); a.alu(13,6,17)
    a.compare(3,13,3); a.branch(4,tag+'disjoint')
    a.compare(6,12,4); a.branch(4,'done'); a.label(tag+'disjoint')

def bayer(layout):
    a=begin(); i=a.immediate
    # Only reached through checked transform; no unchecked public hook.
    a.const(20,addr(layout,'bayer_table')); i(0x27,21,0,0); move(a,22,3)
    a.label('row'); i(0x29,12,21,7); i(0x2e,12,12,3); a.alu(23,20,12); i(0x27,24,0,0)
    a.label('px'); i(0x29,12,24,7); a.alu(12,23,12); i(0x23,13,12,0)
    i(0x23,12,22,0); a.compare(12,13,3); i(0x27,12,0,0); a.branch(3,'black'); i(0x27,12,0,255)
    a.label('black'); a.store(12,22,0,0x36); i(0x27,22,22,1); i(0x27,24,24,1)
    a.compare(24,4,4); a.branch(4,'px'); i(0x27,21,21,1); a.compare(21,5,4); a.branch(4,'row')
    return end(a)

def transform(layout,origin):
    """r3 Y, r4 w, r5 h, r6 UV, r7 context (0 preview/1 capture/2 print), r8 mode.
    Nothing mutates before validation and scratch allocation. All registers preserved.
    """
    a=begin(); i=a.immediate
    i(0x2f,2,8,3); a.branch(4,'done'); check_planes(a)
    a.const(18,addr(layout,'state')); i(0x21,12,18,8); i(0x2f,1,12,0); a.branch(4,'done')
    # Plain photos stay color with printing off. Print preview/capture use penguin gray.
    i(0x2f,1,8,0); a.branch(4,'enabled'); i(0x2f,0,7,2); a.branch(4,'enabled')
    a.const(12,G); i(0x23,12,12,95); i(0x2f,0,12,0); a.branch(4,'done')
    a.label('enabled'); move(a,22,8); i(0x27,20,0,0)
    i(0x2f,0,22,0); a.branch(4,'allocated')
    i(0x27,12,0,12); a.alu(3,4,12,0x306); i(0x27,3,3,768); i(0x27,4,0,32)
    a.call(origin,MALLOC); move(a,20,11); i(0x2f,0,20,0); a.branch(4,'done')
    a.label('allocated'); i(0x27,12,0,1); a.store(12,18,8)
    args(a,(3,4,5,6)); a.call(origin,addr(layout,'orange'))
    i(0x2f,0,22,0); a.branch(4,'neutral')
    i(0x27,12,0,2); i(0x2f,1,22,1); a.branch(4,'curve_ready'); i(0x27,12,0,0)
    a.label('curve_ready'); a.store(12,18,4)
    args(a,(3,4,5)); a.alu(4,4,5,0x306); move(a,5,20); i(0x27,6,0,4)
    args(a,(7,)); i(0x2f,1,7,2); a.branch(4,'not_print'); i(0x27,6,0,8); a.label('not_print')
    a.const(12,65535*4); a.compare(4,12,5); a.branch(4,'step_ok'); i(0x27,6,0,16)
    a.const(12,65535*16); a.compare(4,12,5); a.branch(4,'step_ok'); i(0x27,6,0,64)
    a.label('step_ok'); i(0x27,7,0,0); i(0x27,8,0,0); a.call(origin,addr(layout,'curve'))
    args(a,(3,4,5)); i(0x27,6,20,768)
    i(0x2f,1,22,1); a.branch(4,'not_bayer'); a.call(origin,addr(layout,'bayer')); a.branch(0,'neutral')
    a.label('not_bayer'); i(0x2f,1,22,2); a.branch(4,'floyd'); a.call(origin,addr(layout,'half')); a.branch(0,'neutral')
    a.label('floyd'); a.call(origin,addr(layout,'floyd'))
    a.label('neutral'); args(a,(3,4,5,6)); a.alu(16,4,5,0x306); i(0x2e,17,16,0x41)
    move(a,12,6); a.alu(13,6,17); i(0x27,14,0,128)
    a.label('uv'); a.store(14,12,0,0x36); i(0x27,12,12,1); a.compare(12,13,4); a.branch(4,'uv')
    move(a,4,16); a.call(origin,FLUSH); args(a,(6,)); move(a,3,6); args(a,(4,5)); a.alu(4,4,5,0x306); i(0x2e,4,4,0x41); a.call(origin,FLUSH)
    a.store(0,18,8); i(0x2f,0,20,0); a.branch(4,'done'); move(a,3,20); a.call(origin,FREE)
    a.label('done'); return end(a)

def cleanup(layout,origin):
    a=begin(); i=a.immediate; a.const(18,addr(layout,'state')); i(0x21,3,18,12)
    a.store(0,18,12); i(0x2f,0,3,0); a.branch(4,'done'); a.call(origin,FREE)
    a.label('done'); return end(a)

def init(layout,origin):
    a=begin(); a.call(origin,addr(layout,'cleanup')); a.const(12,addr(layout,'state'))
    for n in (0,4,8,16,20,24,28): a.store(0,12,n)
    restore(a,REGS); a.emit(0xd7e117f0); a.tail(origin,0x0200819c); return a.finish()

def exit_photo(layout,origin):
    a=begin(); a.call(origin,addr(layout,'cleanup')); restore(a,REGS)
    a.emit(0xd7e117f8); a.tail(origin,0x02008270); return a.finish()

def controls(layout,origin,delta):
    a=begin(); i=a.immediate
    i(0x2f,1,4,1); a.branch(4,'done'); i(0x21,12,5,0); i(0x2f,1,12,2); a.branch(4,'done')
    a.const(18,G); i(0x23,12,18,88); i(0x2f,1,12,1); a.branch(4,'done')
    # Do not change effects during countdown/capture.
    i(0x23,12,18,109); i(0x2f,0,12,1); a.branch(4,'done')
    a.const(22,addr(layout,'state')); i(0x21,20,22,0); i(0x27,20,20,delta)
    i(0x2f,12,20,-7); a.branch(4,'zero'); i(0x2f,10,20,3); a.branch(3,'valid')
    a.label('zero'); i(0x27,20,0,0)
    a.label('valid'); a.store(20,22,0)
    for off in (89,90,91,93): a.store(0,18,off,0x36)
    i(0x27,3,0,0); a.call(origin,0x02056870)
    i(0x27,3,0,0); a.call(origin,0x020444e0)
    i(0x27,3,0,8); i(0x27,4,0,0); a.call(origin,0x020543b4)
    i(0x2f,11,20,0); a.branch(4,'refresh')
    i(0x27,12,0,3); a.store(12,18,89,0x36)
    a.alu(20,0,20,2); i(0x27,20,20,-1); a.store(20,18,94,0x36)
    i(0x2e,20,20,2); a.const(12,0x020bbbd4); a.alu(20,20,12); i(0x21,3,20,0)
    a.call(origin,0x02043d20)
    a.label('refresh'); args(a,(3,)); a.call(origin,0x02008b1c); args(a,(3,)); a.call(origin,0x020089a8)
    a.label('done'); restore(a,REGS); i(0x27,11,0,0); ret(a); return a.finish()

def preview(layout,origin):
    a=begin(); i=a.immediate
    # Descriptor belongs to SDK; require RAM descriptor bounds before dereferencing.
    a.const(12,0x02000000); a.compare(3,12,4); a.branch(4,'done')
    a.const(12,RAM_END-64); a.compare(3,12,2); a.branch(4,'done')
    move(a,18,3); i(0x21,3,18,4); i(0x21,6,18,8); i(0x25,4,18,32); i(0x25,5,18,34)
    a.const(12,addr(layout,'state')); i(0x21,8,12,0); i(0x2f,11,8,0); a.branch(4,'mode'); i(0x27,8,0,0)
    a.label('mode'); i(0x27,7,0,0); a.call(origin,addr(layout,'transform'))
    a.label('done'); restore(a,REGS); a.tail(origin,0x020391c0); return a.finish()

def capture(layout,origin):
    a=begin(); i=a.immediate; a.call(origin,addr(layout,'cleanup'))
    a.const(18,CAPTURE); i(0x25,4,18,12); i(0x25,5,18,14); i(0x21,12,18,40)
    i(0x2f,1,12,2); a.branch(4,'other'); i(0x21,3,18,24); i(0x21,6,18,28); a.branch(0,'planes')
    a.label('other'); i(0x21,3,18,32); i(0x21,6,18,36)
    a.label('planes'); check_planes(a,'c'); a.const(26,addr(layout,'state')); i(0x21,8,26,0)
    i(0x2f,11,8,0); a.branch(4,'selected'); i(0x27,8,0,0)
    a.label('selected'); move(a,20,3); move(a,28,6); move(a,22,4); move(a,30,5); move(a,24,8)
    # Before saving a dithered JPEG, retain raw camera pixels for dot-resolution printing.
    i(0x2f,0,24,0); a.branch(4,'apply'); a.const(12,G); i(0x23,12,12,95)
    i(0x2f,0,12,0); a.branch(4,'apply')
    a.alu(3,16,17); i(0x27,4,0,32); a.call(origin,MALLOC)
    i(0x2f,0,11,0); a.branch(4,'oom'); move(a,14,11); a.store(14,26,12); a.alu(16,22,30,0x306); i(0x2e,17,16,0x41)
    a.store(22,26,16); a.store(30,26,20); a.store(24,26,24)
    # Explicit byte copy also supports separate Y and UV allocations.
    move(a,12,20); move(a,13,14); a.alu(14,20,16)
    a.label('copy_y'); i(0x23,15,12,0); a.store(15,13,0,0x36); i(0x27,12,12,1); i(0x27,13,13,1)
    a.compare(12,14,4); a.branch(4,'copy_y'); move(a,12,28); a.alu(14,28,17)
    a.label('copy_uv'); i(0x23,15,12,0); a.store(15,13,0,0x36); i(0x27,12,12,1); i(0x27,13,13,1)
    a.compare(12,14,4); a.branch(4,'copy_uv'); a.branch(0,'apply')
    a.label('oom'); i(0x27,12,0,1); a.store(12,26,28)
    # Leave capture intact on allocation failure. The print adapter can still dither its JPEG.
    a.branch(0,'done')
    a.label('apply'); move(a,3,20); move(a,6,28); move(a,4,22); move(a,5,30); move(a,8,24)
    i(0x27,7,0,1); a.call(origin,addr(layout,'transform'))
    a.label('done'); return end(a)

def resample(layout,origin):
    """Private bounded YUV420 nearest-neighbor raw snapshot -> print buffer.
    r3 dstY,r4 dw,r5 dh,r6 dstUV,r7 srcY,r8 sw,r10 sh. Called only after ownership checks.
    """
    a=begin(); i=a.immediate
    a.alu(11,8,10,0x306); a.alu(11,7,11) # srcUV
    for uv in (False,True):
        tag='uv' if uv else 'y'; i(0x27,18,0,0)
        if uv: i(0x2e,5,5,0x41); i(0x2e,10,10,0x41)
        a.label(tag+'row'); a.alu(19,18,10,0x306); a.alu(19,19,5,0x30a); a.alu(19,19,8,0x306)
        a.alu(19,19,11 if uv else 7); i(0x27,20,0,0)
        a.label(tag+'px'); a.alu(21,20,8,0x306); a.alu(21,21,4,0x30a)
        if uv: a.const(22,0xfffffffe); a.alu(21,21,22,3)
        a.alu(21,19,21); i(0x23,22,21,0); a.store(22,6 if uv else 3,0,0x36)
        if uv: i(0x23,22,21,1); a.store(22,6,1,0x36)
        i(0x27,6 if uv else 3,6 if uv else 3,2 if uv else 1); i(0x27,20,20,2 if uv else 1)
        a.compare(20,4,4); a.branch(4,tag+'px'); i(0x27,18,18,1); a.compare(18,5,4); a.branch(4,tag+'row')
    return end(a)

def transpose(layout,origin):
    """r3 src,row-major r4 sw,r5 sh, r6 dst. Clockwise rotation into head/feed layout.
    Forward: dst[(383-x)*sh+y] = src[y*384+x]. Reverse uses r7=1.
    """
    a=begin(); i=a.immediate; i(0x27,18,0,0)
    a.label('row'); i(0x27,19,0,0)
    a.label('px'); a.alu(20,18,4,0x306); a.alu(20,20,19); a.alu(20,3,20)
    i(0x27,21,4,-1); a.alu(21,21,19,2); a.alu(21,21,5,0x306); a.alu(21,21,18); a.alu(21,6,21)
    i(0x2f,0,7,0); a.branch(4,'forward'); i(0x23,22,21,0); a.store(22,20,0,0x36); a.branch(0,'next')
    a.label('forward'); i(0x23,22,20,0); a.store(22,21,0,0x36)
    a.label('next'); i(0x27,19,19,1); a.compare(19,4,4); a.branch(4,'px')
    i(0x27,18,18,1); a.compare(18,5,4); a.branch(4,'row'); return end(a)

def printer(layout,origin):
    a=begin(); i=a.immediate
    # Stock decoded print image is contiguous YUV420. Guard and require a 384-dot side.
    a.alu(12,4,5,0x306); a.alu(6,3,12); check_planes(a,'p')
    i(0x2f,0,4,384); a.branch(4,'head_ok'); i(0x2f,1,5,384); a.branch(4,'done')
    a.label('head_ok'); a.const(18,addr(layout,'state')); i(0x21,22,18,0)
    i(0x2f,11,22,0); a.branch(4,'selected'); i(0x27,22,0,0)
    a.label('selected'); i(0x21,7,18,12); i(0x2f,0,7,0); a.branch(4,'apply')
    i(0x21,8,18,16); i(0x21,10,18,20); i(0x21,22,18,24)
    a.call(origin,addr(layout,'resample')); a.call(origin,addr(layout,'cleanup'))
    a.label('apply'); args(a,(3,4,5)); a.alu(12,4,5,0x306); a.alu(6,3,12)
    i(0x27,7,0,2); move(a,8,22); a.call(origin,addr(layout,'transform'))
    i(0x2f,1,22,0); a.branch(4,'date')
    i(0x2f,1,5,384); a.branch(4,'rotated'); a.call(origin,addr(layout,'tone')); a.branch(0,'date')
    a.label('rotated'); a.alu(3,4,5,0x306); i(0x27,4,0,32); a.call(origin,MALLOC); move(a,20,11)
    i(0x2f,0,20,0); a.branch(4,'date'); args(a,(3,4,5)); move(a,6,20); i(0x27,7,0,0)
    a.call(origin,addr(layout,'transpose')); move(a,3,20); move(a,4,5); i(0x27,5,0,384)
    a.call(origin,addr(layout,'tone')); args(a,(3,4,5)); move(a,6,20); i(0x27,7,0,1)
    a.call(origin,addr(layout,'transpose')); move(a,3,20); a.call(origin,FREE)
    a.label('date'); args(a,(3,4,5)); a.call(origin,addr(layout,'date'))
    a.alu(4,4,5,0x306); a.call(origin,FLUSH)
    a.label('done'); restore(a,REGS); i(0x27,7,0,0) # existing grayscale head, untouched heat limits
    a.emit(0xd7e197e0); a.tail(origin,0x0205831c); return a.finish()

def labels(layout,origin):
    """OSD-only 8-bit pixels. Never receives or writes a photo/print plane."""
    a=begin(); i=a.immediate
    i(0x2f,1,3,0); a.branch(4,'done'); a.const(12,G); i(0x23,12,12,88)
    i(0x2f,1,12,1); a.branch(4,'done'); a.const(18,addr(layout,'state')); i(0x21,19,18,0)
    i(0x2f,12,19,1); a.branch(3,'positive'); i(0x27,19,0,0); a.label('positive'); i(0x2f,2,19,3); a.branch(4,'done')
    # Only SDK-owned OSD buffers, exact format and allocation size.
    a.const(20,OSD); i(0x21,12,20,8); a.const(13,0x014000f0); a.compare(12,13,1); a.branch(4,'done')
    for off,n,nextlabel in ((16,20,'second'),(24,28,'done')):
        i(0x21,12,20,off); a.compare(4,12,1); a.branch(4,nextlabel)
        i(0x21,12,20,n); a.const(13,76800); a.compare(12,13,1); a.branch(4,'done'); a.branch(0,'owned')
        if nextlabel=='second': a.label('second')
    a.label('owned'); a.const(12,HEAP); a.compare(4,12,4); a.branch(4,'done')
    a.const(12,RAM_END-76800); a.compare(4,12,2); a.branch(4,'done')
    move(a,18,19); a.call(origin,0x0202a868); args(a,(4,)); move(a,19,18)
    # Own only preview-label X 0..223, Y 32..63 (full cache lines).
    # The stock information bar Y 0..31 is never written.
    i(0x27,20,0,0); i(0x27,21,0,249); i(0x2f,0,19,0); a.branch(4,'bar_ready'); i(0x27,21,0,249); a.label('bar_ready')
    a.label('barx'); i(0x27,12,0,319); a.alu(12,12,20,2); i(0x27,13,0,240); a.alu(12,12,13,0x306); a.alu(12,4,12)
    i(0x27,22,0,32); a.label('bary'); a.store(21,12,32,0x36); i(0x27,12,12,1); i(0x27,22,22,-1)
    i(0x2f,1,22,0); a.branch(4,'bary'); i(0x27,20,20,1); i(0x2f,1,20,224); a.branch(4,'barx')
    i(0x2f,0,19,0); a.branch(4,'flush'); i(0x27,19,19,-1); i(0x2e,19,19,5); a.const(12,addr(layout,'names')); a.alu(19,19,12)
    a.const(20,addr(layout,'font')); i(0x27,21,0,4); i(0x27,22,0,251)
    a.label('char'); i(0x23,12,19,0); i(0x2f,0,12,0); a.branch(4,'flush')
    i(0x27,13,0,5); a.alu(12,12,13,0x306); a.alu(23,20,12); i(0x27,24,0,0)
    a.label('col'); a.alu(12,23,24); i(0x23,25,12,0); i(0x27,26,0,0)
    a.label('bit'); i(0x29,12,25,1); i(0x2f,0,12,0); a.branch(4,'skip')
    i(0x2e,12,24,1); a.alu(12,21,12); i(0x27,13,0,319); a.alu(12,13,12,2)
    i(0x27,13,0,240); a.alu(12,12,13,0x306); a.alu(12,4,12)
    i(0x2e,13,26,1); i(0x27,13,13,40); a.alu(12,12,13)
    for off in (0,1,-240,-239): a.store(22,12,off,0x36)
    a.label('skip'); i(0x2e,25,25,0x41); i(0x27,26,26,1); i(0x2f,1,26,7); a.branch(4,'bit')
    i(0x27,24,24,1); i(0x2f,1,24,5); a.branch(4,'col')
    i(0x27,19,19,1); i(0x27,21,21,12); a.branch(0,'char')
    a.label('flush'); move(a,3,4); a.const(4,76800); a.call(origin,FLUSH)
    a.label('done'); restore(a,REGS); a.tail(origin,0x0203aa34); return a.finish()

GENERATORS={
 'orange':lambda l,o:color_gray.orange(BASE,SIZE),
 'curve':lambda l,o:gray_filters.curve_sub(BASE,o),
 'bayer':lambda l,o:bayer(l),
 'half':lambda l,o:effects_v5_target.halftone_kernel(BASE,MAX_DIM,SIZE,table=l['half_table']),
 'floyd':lambda l,o:diffusion_target.make_kernel('floyd',o,max_dimension=MAX_DIM,optimized=True),
 'tone':lambda l,o:tone_gray.native(BASE,tone_gray.tables()),
 'date':lambda l,o:date_stamp.stamp(BASE,clock=0x020c7c74,setting=0x020c1ab8),
 'transform':transform,'cleanup':cleanup,'init':init,'exit':exit_photo,
 'left':lambda l,o:controls(l,o,-1),'right':lambda l,o:controls(l,o,1),
 'preview':preview,'capture':capture,'resample':resample,'transpose':transpose,'printer':printer,'labels':labels,
}

def payload():
    layout={k:0 for k in (*GENERATORS,'state','bayer_table','half_table','luts','font','date_font','names','tv','col')}
    def configure():
        gray_filters.CURVE_STATE=layout['state']+4; gray_filters.LUTS=layout['luts']
        date_stamp.STAMP_FONT=layout['date_font']; tone_gray.T_TABLE=layout['tv']; tone_gray.COL_TABLE=layout['col']
    # All absolute references are fixed movhi/ori pairs; converge deterministic sizes.
    for iteration in range(4):
        configure(); chunks={}; pos=0; fresh={}
        for k,f in GENERATORS.items():
            pos=(pos+15)&~15; fresh[k]=pos; chunks[k]=f(layout,BASE+pos); pos+=len(chunks[k])
        data={'state':bytes(32),'bayer_table':bayer8_thresholds(),'half_table':effects_v5_target.halftone_table(),
              'luts':b''.join(gray_filters.curves()),'font':preview_labels.font_data(),'date_font':date_stamp.font(),
              'names':b''.join(n.encode().ljust(32,b'\0') for n in NAMES),
              'tv':struct.pack('<256H',*tone_gray.tables()['TV']), 'col':struct.pack('<384h',*tone_gray.tables()['COL'])}
        for k,b in data.items(): pos=(pos+15)&~15; fresh[k]=pos; chunks[k]=b; pos+=len(b)
        if fresh==layout: break
        layout=fresh
    else: raise ValueError('layout did not converge')
    if pos>SIZE: raise ValueError(f'payload {pos} exceeds reclaimed table {SIZE}')
    out=bytearray(SIZE)
    for k,b in chunks.items(): out[layout[k]:layout[k]+len(b)]=b
    return bytes(out),layout,{k:len(v) for k,v in chunks.items()}

def build():
    stock=(ROOT/'flash_read1.bin').read_bytes(); source=(ROOT/'analysis/build_01/image.bin').read_bytes()
    if digest(stock)!=STOCK_SHA or digest(source)!=BASE_SHA: raise ValueError('source hash changed')
    code,layout,lengths=payload(); image=bytearray(source); patches=[]
    def patch(off,b,description,expected=None):
        old=source[off:off+len(b)]
        if old!=stock[off:off+len(b)]: raise ValueError('application preimage differs from stock')
        if expected is not None and old!=struct.pack('<I',expected): raise ValueError(f'bad preimage {off:#x}')
        if off<0x2600 or off+len(b)>0xc4000: raise ValueError('outside application')
        image[off:off+len(b)]=b; patches.append(dict(offset=off,length=len(b),description=description,before_sha256=digest(old),after_sha256=digest(b)))
    patch(CAVE,code,'Replace retired sketch lookup table with native effects code and read-only tables')
    for off,key,link,expected in ((0xb650,'left',False,0xd7e197f4),(0xb978,'right',False,None),
        (0xa86c,'exit',False,0xd7e117f8),(0xa798,'init',False,0xd7e117f0),(0xa9d4,'preview',True,0x0400c37b),
        (0x41dfc,'capture',True,0x1860020c),(0x5a918,'printer',False,0xd7e197e0),(0x6e6c,'labels',True,None)):
        patch(off,branch(off+BIAS,addr(layout,key),link),'Hook '+key,expected)
    patch(0x41e00,branch(0x41e00+BIAS,0x41ea0+BIAS),'Skip retired kaleidoscope and both sketch modes',0xa863ee44)
    for off,expected,description in ((0x23d98,None,'Retire sketch renderer'),(0x5aef8,None,'Retire stock pre-print enhancement')):
        patch(off,struct.pack('<I',0x44004800),description,expected)
    patch(0xbf18,branch(0xbf18+BIAS,0xbf20+BIAS),'Print toggle preserves custom effect selection',0x9c600007)
    patch(0xab1c,struct.pack('<I',0xab000000),'Disable stock capture date: file preparation',0xab0b0000)
    patch(0xac74,struct.pack('<I',0xab400000),'Disable stock memory date flag register',0xab4b0000)
    patch(0xac78,struct.pack('<I',0xa7000000),'Disable stock capture date: memory preparation',0xa70b00ff)
    patch(0x4b318,struct.pack('<I',0x9c600000),'Select stock mass storage USB descriptors with or without SD',0xa46300ff)
    # Boot header/stub, resources and persistent settings are exact base-image bytes.
    assert image[:0x2600]==source[:0x2600] and image[0xc4000:]==source[0xc4000:]
    sectors=[off for off in range(0,len(image),4096) if image[off:off+4096]!=source[off:off+4096]]
    assert 0 not in sectors and 0x2fd000 not in sectors and all(0x2000<=o<0xc4000 for o in sectors)
    return bytes(image),dict(schema=1,status='OFFLINE_CANDIDATE_NOT_HARDWARE_VERIFIED',source_sha256=BASE_SHA,stock_sha256=STOCK_SHA,
        image_sha256=digest(image),payload_base=BASE,payload_size=SIZE,payload_used=max(layout[k]+lengths[k] for k in layout),
        symbols={k:dict(ram=addr(layout,k),flash=CAVE+layout[k],length=lengths[k]) for k in layout},
        changed_sectors=[dict(offset=o,before_sha256=digest(source[o:o+4096]),after_sha256=digest(image[o:o+4096])) for o in sectors],
        patches=patches,features=dict(frames=7,effects=list(NAMES),labels='White text on transparent OSD, below unchanged stock information bar',usb='Always stock MSC interface 4',
        gray='Penguin Q8 Cr/Cb 124/-13',printer_curve='Penguin input_shift measured model; stock heat guards retained',date='YYYY-MM-DD; original print-date setting'),
        hardware_pending=['preview/capture buffer ownership and geometry','UI/frame switching and label palette','print orientation/tone/date',
        'raw-snapshot allocation headroom','USB enumeration and vendor access with SD absent'])

def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--output',type=Path,default=ROOT/'analysis/build_02'); a=p.parse_args()
    image,manifest=build(); a.output.mkdir(parents=True,exist_ok=False)
    (a.output/'image.bin').write_bytes(image); (a.output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({k:manifest[k] for k in ('status','image_sha256','payload_used')},indent=2)); print('changed sectors',len(manifest['changed_sectors']))
if __name__=='__main__': main()
