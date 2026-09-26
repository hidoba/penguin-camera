"""Volatile native Random penguin worker, with a small resident JPEG subset.

Never writes flash/SD. A fresh menu press selects one image; original guarded
printer controls heating/motor. Dry runs decode/correct/free without printing.
Malformed decoder returns latch the worker busy until power-off, rather than
freeing an untrusted pointer or repeating a potentially leaking operation.
"""
import struct
from live_preview_patch import Code,BIAS
from or1k_subset import branch
from menu_controller import ACTIVATE,DISPLAY,STATUS,SELECTION

EVENT=0x800
STATE=0xc00
DEBOUNCE_WORD=48  # STATE+48: uptime at the end of the last print (update 32; diagnostic-only word before)
TABLE=0xd00
LUT=0xe00
DATA=0x1000


GUARD_NAMES=('worker_busy','index','print_flag','printer_busy','print_mode',
             'not_armed','dry_mode','settings','density','lcd')


def worker(base,size,count,menu_start,menu_size,input_disjoint=False,diagnostics=False,max_power_state=5,rotate180=False,table=TABLE):
    # Preserve historical candidate generation unless the battery-state fix is
    # explicitly selected. Stock printer handles states 5 and 6 identically.
    if max_power_state not in (5,6):raise ValueError('Unverified power-state limit')
    a=Code();imm=a.immediate
    saved=tuple(r for r in range(2,31) if r!=11)
    imm(0x27,1,1,-160)
    for i,r in enumerate(saved):a.store(r,1,16+i*4)
    a.store(4,1,4)
    a.const(22,base+STATE)
    def guard(name):a.branch(4,'reject_'+name if diagnostics else 'reject')
    imm(0x21,12,22,0);imm(0x2f,1,12,0);guard('worker_busy')
    imm(0x2f,3,3,count);guard('index')
    imm(0x2f,2,4,1);guard('print_flag')
    a.const(12,0x02088414);imm(0x21,12,12,0);imm(0x2f,1,12,0);guard('printer_busy')
    a.const(12,0x02085e9c);imm(0x21,12,12,0)
    imm(0x2f,0,4,0);a.branch(4,'dry_mode')
    imm(0x2f,1,12,6);guard('print_mode')
    imm(0x21,12,22,40);imm(0x2f,1,12,1);guard('not_armed');a.branch(0,'settings')
    a.label('dry_mode');imm(0x2f,1,12,9);guard('dry_mode')
    a.label('settings')
    a.const(12,0x020892c9);imm(0x23,12,12,0);imm(0x2f,2,12,max_power_state);guard('settings');a.store(12,1,144)
    a.const(12,0x02089344);imm(0x23,12,12,0);imm(0x2f,2,12,3);guard('density');a.store(12,1,148)
    a.const(12,0x02086970);imm(0x23,12,12,0);imm(0x2f,2,12,1);guard('lcd');a.store(12,1,140)
    if diagnostics:
        a.store(0,22,44)
    imm(0x27,12,0,1);a.store(12,22,0);a.store(12,22,8)
    a.store(3,22,16);imm(0x2e,12,3,4);a.const(13,base+table);a.alu(24,12,13)
    imm(0x21,20,24,8);imm(0x21,12,24,12);a.store(12,22,20)
    a.store(0,1,132);a.store(0,1,136);a.store(0,1,0)
    imm(0x21,3,24,0);imm(0x27,4,1,132);imm(0x27,5,1,136);imm(0x27,6,1,138)
    imm(0x21,7,24,4);imm(0x27,8,0,0);a.call(base,BIAS+0x4ba2c)
    a.store(11,1,128);imm(0x21,18,1,132);a.store(18,22,24)
    imm(0x21,12,1,136);a.store(12,22,28)
    imm(0x2f,0,18,0);a.branch(4,'null_output')
    imm(0x25,12,1,136);a.compare(12,20);a.branch(4,'unsafe_output')
    imm(0x25,12,1,138);imm(0x2f,1,12,384);a.branch(4,'unsafe_output')
    a.const(12,0x0210e200);a.compare(18,12,4);a.branch(4,'unsafe_output')
    imm(0x29,12,18,31);imm(0x2f,1,12,0);a.branch(4,'unsafe_output')
    imm(0x27,12,0,576);a.alu(26,20,12,0x306);a.alu(28,18,26)
    a.compare(28,18,5);a.branch(4,'unsafe_output')
    a.const(12,0x021ff000);a.compare(28,12,2);a.branch(4,'unsafe_output')
    # Explicitly exclude live patch allocations, in addition to display/stack.
    for n,(lo,hi) in enumerate(((base,base+size),(menu_start,menu_start+menu_size))):
        a.const(12,hi);a.compare(18,12,3);a.branch(4,f'disjoint{n}')
        a.const(12,lo);a.compare(28,12,2);a.branch(4,'unsafe_output');a.label(f'disjoint{n}')
    if input_disjoint:
        # Flash-backed wrapper separately owns the compressed JPEG allocation.
        # Never accept an aliased decoder output and later double-free that RAM.
        imm(0x21,12,24,0);imm(0x21,13,24,4);imm(0x27,13,13,15)
        a.const(15,0xfffffff0);a.alu(13,13,15,3);a.alu(13,12,13)
        a.compare(18,13,3);a.branch(4,'jpeg_disjoint')
        a.compare(28,12,2);a.branch(4,'unsafe_output');a.label('jpeg_disjoint')
    imm(0x21,11,1,128);imm(0x2f,1,11,0);a.branch(4,'free')
    # The host-verified JPEG is already column-oriented. Correct Y in place.
    imm(0x27,12,0,384);a.alu(13,20,12,0x306);a.alu(13,18,13)
    imm(0x2a,12,18,0);a.const(15,base+LUT)
    if rotate180:
        # Reversing the contiguous feed*384 luminance plane rotates both axes.
        # Fuse the symmetric swap with the brightness lookup: no extra image
        # allocation or JPEG re-encode. Chroma is unused by this gray printer.
        # The validated width and fixed even height guarantee an even count.
        imm(0x27,13,13,-1)
        a.label('curve');imm(0x23,16,12,0);imm(0x23,17,13,0)
        a.alu(16,15,16);a.alu(17,15,17)
        imm(0x23,16,16,0);imm(0x23,17,17,0)
        a.store(17,12,op=0x36);a.store(16,13,op=0x36)
        imm(0x27,12,12,1);imm(0x27,13,13,-1)
        a.compare(12,13,4);a.branch(4,'curve')
    else:
        a.label('curve');imm(0x23,16,12,0);a.alu(16,15,16);imm(0x23,16,16,0)
        a.store(16,12,op=0x36);imm(0x27,12,12,1);a.compare(12,13,4);a.branch(4,'curve')
    imm(0x2a,3,18,0);imm(0x2a,4,26,0);a.call(base,BIAS+0x29790)
    imm(0x21,12,1,4);imm(0x2f,0,12,0);a.branch(4,'free')
    a.const(12,0x02085e9c);imm(0x21,12,12,0);imm(0x2f,1,12,6);a.branch(4,'print_refused')
    a.const(12,0x02088414);imm(0x21,12,12,0);imm(0x2f,1,12,0);a.branch(4,'print_refused')
    imm(0x27,3,0,0);a.call(base,BIAS+0x4bed8)
    imm(0x2a,3,18,0);imm(0x2a,4,20,0);imm(0x27,5,0,384)
    imm(0x21,6,1,148);imm(0x27,7,0,0);imm(0x21,8,1,144)
    a.call(base,BIAS+0x4cf20);a.store(11,1,128)
    imm(0x27,3,0,1);a.call(base,BIAS+0x4bed8);a.branch(0,'free')
    a.label('print_refused');imm(0x27,12,0,8);a.store(12,1,128)
    a.label('free');imm(0x2a,3,18,0);a.call(base,BIAS+0x3adb0);a.branch(0,'restore')
    a.label('null_output');imm(0x21,12,1,128);imm(0x2f,1,12,0);a.branch(4,'restore')
    imm(0x27,12,0,8);a.store(12,1,128);a.branch(0,'restore')
    a.label('unsafe_output');imm(0x27,12,0,8);a.store(12,1,128)
    imm(0x27,12,0,0x7f);a.store(12,22,8)  # busy latch retained
    a.label('restore');imm(0x21,3,1,140);a.call(base,BIAS+0x368fc)
    imm(0x21,11,1,128);a.store(11,22,4)
    imm(0x21,12,22,8);imm(0x2f,0,12,0x7f);a.branch(4,'done')
    a.store(0,22,0);imm(0x27,12,0,2);a.store(12,22,8)
    imm(0x21,12,22,12);imm(0x27,12,12,1);a.store(12,22,12);a.branch(0,'done')
    a.label('reject');imm(0x27,11,0,8)
    a.label('done')
    for i,r in enumerate(saved):imm(0x21,r,1,16+i*4)
    imm(0x27,1,1,160);a.emit(0x44004800)
    if diagnostics:
        for number,name in enumerate(GUARD_NAMES,1):
            a.label('reject_'+name)
            # Only previously reserved diagnostic words. Preserve rejection
            # behavior, all guards, caller registers and the original status 8.
            imm(0x27,11,0,number);a.store(11,22,44)
            a.store(3,22,48);a.store(4,22,52);a.store(12,22,56);a.store(1,22,60)
            a.branch(0,'reject')
    code=a.finish()
    if len(code)>EVENT:raise ValueError('worker overflow')
    return code


def event(base,count,virtual,worker_target=None,busy_address=None,*,random_timing=False,debounce_ms=None):
    a=Code();imm=a.immediate;origin=base+EVENT
    # Camera/Settings and invalid events retain the exact existing handler.
    a.const(11,SELECTION);imm(0x23,11,11,0);imm(0x2f,1,11,2);a.branch(4,'fallback')
    imm(0x2f,1,4,1);a.branch(4,'fallback')
    a.const(11,0x02000000);a.compare(5,11,4);a.branch(4,'fallback')
    a.const(11,0x021ffffc);a.compare(5,11,2);a.branch(4,'fallback')
    imm(0x29,11,5,3);imm(0x2f,1,11,0);a.branch(4,'fallback')
    imm(0x21,11,5,0);imm(0x2f,1,11,0);a.branch(4,'fallback')
    saved=tuple(r for r in range(2,31) if r!=11)
    imm(0x27,1,1,-112)
    for i,r in enumerate(saved):a.store(r,1,i*4)
    a.const(18,base+STATE);imm(0x21,12,18,0);imm(0x2f,1,12,0);a.branch(4,'done')
    if busy_address is not None:
        a.const(12,busy_address);imm(0x21,12,12,0);imm(0x2f,1,12,0);a.branch(4,'done')
    if debounce_ms:
        # Update 32: presses queued while a print was running (or a double press)
        # arrive after it finished; ignore activations within debounce_ms of the end
        # of the previous print (uptime word 0x0208671c, ms; STATE+48 = end time).
        from penguin_random import TICK_ADDRESS
        a.const(12,TICK_ADDRESS);imm(0x21,12,12,0);imm(0x21,13,18,DEBOUNCE_WORD)
        a.alu(12,12,13,2);a.const(13,debounce_ms);a.compare(12,13,4);a.branch(4,'done')
    if random_timing:
        from penguin_random import selection
        selection(a,count)
    else:
        # Historical LCG mixes redraw count, NOT elapsed press timing.
        imm(0x21,12,18,32);a.const(13,1664525);a.alu(12,12,13,0x306)
        a.const(13,1013904223);a.alu(12,12,13);a.const(13,virtual+STATUS)
        imm(0x21,13,13,4);a.alu(12,12,13,5);a.store(12,18,32)
        imm(0x27,13,0,count);a.alu(15,12,13,0x30a);a.alu(15,15,13,0x306);a.alu(3,12,15,2)
        imm(0x21,12,18,16);a.compare(3,12);a.branch(4,'selected')
        imm(0x27,3,3,1);imm(0x2f,4,3,count);a.branch(4,'selected');imm(0x27,3,0,0)
    a.label('selected');imm(0x27,4,0,1);a.call(origin,base if worker_target is None else worker_target)
    a.call(origin,virtual+DISPLAY)
    if debounce_ms:
        from penguin_random import TICK_ADDRESS
        a.const(12,TICK_ADDRESS);imm(0x21,12,12,0);a.store(12,18,DEBOUNCE_WORD)
    a.label('done')
    for i,r in enumerate(saved):imm(0x21,r,1,i*4)
    imm(0x27,1,1,112);imm(0x27,11,0,0);a.emit(0x44004800)
    a.label('fallback')
    code=a.finish()+branch(origin+len(a.words)*4,virtual+ACTIVATE)
    if len(code)>STATE-EVENT:raise ValueError('event overflow')
    return code


def build(base,items,menu_start,menu_size,virtual):
    """items: verified (JPEG bytes, feed, source ID), resident test subset only."""
    if not 1<=len(items)<=16:raise ValueError('require 1..16 images')
    size=(DATA+sum((len(j)+31)&~31 for j,_,_ in items)+63)&~63
    if base%64 or not 0x0210e200<=base<=0x021ff000-size:raise ValueError('bad allocation')
    blob=bytearray(size);offset=DATA
    for i,(jpeg,feed,source_id) in enumerate(items):
        if not 4<=len(jpeg)<=131072 or not 32<=feed<=1024 or feed%32:raise ValueError('bad image')
        struct.pack_into('<4I',blob,TABLE+i*16,base+offset,len(jpeg),feed,source_id)
        blob[offset:offset+len(jpeg)]=jpeg;offset+=(len(jpeg)+31)&~31
    for off,code in ((0,worker(base,size,len(items),menu_start,menu_size)),(EVENT,event(base,len(items),virtual))):
        blob[off:off+len(code)]=code
    struct.pack_into('<I',blob,STATE+16,0xffffffff)
    struct.pack_into('<I',blob,STATE+32,0x3295b)
    blob[LUT:LUT+256]=bytes((v*190+127)//255 for v in range(256))
    return bytes(blob)
