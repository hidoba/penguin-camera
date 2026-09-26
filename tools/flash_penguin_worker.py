"""Offline flash-backed Random penguin generator. No hardware installer/writes.

The exact pack's verified directory/CRCs are compiled into trusted code data.
Read only one JPEG into an aligned temporary buffer, CRC-check before decoding,
then call the already tested native decode/correct/guarded-print worker.
"""
import struct
from live_preview_patch import Code,BIAS
from random_penguin_ram import worker,event,EVENT,STATE,TABLE,LUT
from prepare_print_penguins import validate

INNER=0x1000
# Per-photo directories: META (32-byte records: flash offset, bytes, feed, id, CRC)
# and FTABLE (16-byte records: runtime JPEG pointer, bytes, feed, id).
# Up to 16 photos: the original, hardware-proven places. Update 38: 17..64 photos
# use the free tail of the reader slot instead (the original places hold 16).
LEGACY_COUNT=16
META=0x2000
FTABLE=INNER+TABLE
META_EXT=0x400
FTABLE_EXT=0xc00
MAX_COUNT=64                 # = PGPK v2 limit (prepare_print_penguins.validate)


def layout(count):
    """(META, FTABLE) offsets from the worker base for `count` photos."""
    if not 1<=count<=MAX_COUNT:raise ValueError(f'worker directory holds 1..{MAX_COUNT} photos')
    return (META,FTABLE) if count<=LEGACY_COUNT else (META_EXT,FTABLE_EXT)
CONTROL=0x2200
SIZE=0x2400


def reader(base,count,protected_start,protected_size):
    meta,ftable=layout(count)
    a=Code();imm=a.immediate
    saved=tuple(r for r in range(2,31) if r!=11)
    imm(0x27,1,1,-160)
    for i,r in enumerate(saved):a.store(r,1,16+i*4)
    a.store(3,1,4);a.store(4,1,8)
    a.const(22,base+CONTROL)
    imm(0x21,12,22,0);imm(0x2f,1,12,0);a.branch(4,'reject')
    a.const(12,base+INNER+STATE);imm(0x21,13,12,0);imm(0x2f,1,13,0);a.branch(4,'reject')
    imm(0x2f,3,3,count);a.branch(4,'reject');imm(0x2f,2,4,1);a.branch(4,'reject')
    a.const(13,0x02088414);imm(0x21,13,13,0);imm(0x2f,1,13,0);a.branch(4,'reject')
    a.const(13,0x02085e9c);imm(0x21,13,13,0);imm(0x2f,0,4,0);a.branch(4,'dry')
    imm(0x2f,1,13,6);a.branch(4,'reject');imm(0x21,13,12,40);imm(0x2f,1,13,1);a.branch(4,'reject')
    a.branch(0,'begin');a.label('dry');imm(0x2f,1,13,9);a.branch(4,'reject')
    a.label('begin');imm(0x27,12,0,1);a.store(12,22,0)
    imm(0x2e,12,3,5);a.const(20,base+meta);a.alu(20,20,12)
    imm(0x21,26,20,4);imm(0x27,26,26,15);a.const(12,0xfffffff0);a.alu(26,26,12,3)
    imm(0x2a,3,26,0);imm(0x27,4,0,32);a.call(base,BIAS+0x3ace0);imm(0x2a,18,11,0)
    imm(0x2f,0,18,0);a.branch(4,'allocation_failed')
    a.const(12,0x0210e200);a.compare(18,12,4);a.branch(4,'unsafe')
    imm(0x29,12,18,31);imm(0x2f,1,12,0);a.branch(4,'unsafe')
    a.alu(13,18,26);a.compare(13,18,5);a.branch(4,'unsafe')
    a.const(12,0x021ff000);a.compare(13,12,2);a.branch(4,'unsafe')
    a.const(12,protected_start+protected_size);a.compare(18,12,3);a.branch(4,'owned')
    a.const(12,protected_start);a.compare(13,12,2);a.branch(4,'unsafe')
    a.label('owned');a.store(18,22,8)
    # Stock SPI DMA rounds up to 16: allocate/read/invalidate that whole range.
    # Clear and flush before DMA so a partial transfer cannot reuse old JPEG RAM.
    imm(0x2a,12,18,0);imm(0x2a,13,26,0)
    a.label('clear');a.store(0,12);imm(0x27,12,12,4);imm(0x27,13,13,-4)
    imm(0x2f,1,13,0);a.branch(4,'clear')
    imm(0x2a,3,18,0);imm(0x2a,4,26,0);a.call(base,BIAS+0x29790)
    imm(0x21,3,20,0);imm(0x2a,4,18,0);imm(0x2a,5,26,0);a.call(base,BIAS+0x39528)
    imm(0x2a,3,18,0);imm(0x2a,4,26,0);a.call(base,BIAS+0x2980c)
    # Reflected CRC32 over JPEG bytes, not rounded DMA padding.
    imm(0x2a,24,18,0);imm(0x21,26,20,4);a.const(28,0xffffffff);a.const(30,0xedb88320)
    a.label('byte');imm(0x23,12,24,0);a.alu(28,28,12,5);imm(0x27,13,0,8)
    a.label('bit');imm(0x29,12,28,1);imm(0x2e,28,28,0x41);imm(0x2f,0,12,0);a.branch(4,'next')
    a.alu(28,28,30,5);a.label('next');imm(0x27,13,13,-1);imm(0x2f,1,13,0);a.branch(4,'bit')
    imm(0x27,24,24,1);imm(0x27,26,26,-1);imm(0x2f,1,26,0);a.branch(4,'byte')
    a.const(12,0xffffffff);a.alu(28,28,12,5);a.store(28,22,12)
    imm(0x21,12,20,16);a.compare(28,12);a.branch(4,'bad_crc')
    # Fill just the selected mutable JPEG-pointer slot. All other fields are
    # compiled from the validated pack, and inner worker rechecks output bounds.
    imm(0x21,3,1,4);imm(0x2e,12,3,4);a.const(13,base+ftable);a.alu(24,12,13)
    a.store(18,24,0);imm(0x21,4,1,8);a.call(base,base+INNER)
    a.store(11,1,128);a.store(0,24,0)
    a.const(12,base+INNER+STATE);imm(0x21,12,12,0);imm(0x2f,1,12,0);a.branch(4,'unsafe')
    a.branch(0,'free')
    a.label('bad_crc');imm(0x27,12,0,8);a.store(12,1,128)
    a.label('free');imm(0x2a,3,18,0);a.call(base,BIAS+0x3adb0);a.store(0,22,8)
    imm(0x21,11,1,128);a.branch(0,'completed')
    a.label('allocation_failed');imm(0x27,11,0,8)
    a.label('completed');a.store(0,22,0);a.store(11,22,4);a.branch(0,'done')
    a.label('unsafe');imm(0x27,11,0,8);a.store(11,22,4);a.branch(0,'done')  # retain busy latch
    a.label('reject');imm(0x27,11,0,8)
    a.label('done')
    for i,r in enumerate(saved):imm(0x21,r,1,16+i*4)
    imm(0x27,1,1,160);a.emit(0x44004800)
    code=a.finish()
    if len(code)>min(INNER,META_EXT):raise ValueError('reader overflow')
    return code


def build(base,pack,flash_offset,menu_virtual,protected_start,protected_size,*,max_power_state=5,rotate180=False,random_timing=False,debounce_ms=None):
    records=validate(pack);count=len(records)
    meta,ftable=layout(count)
    assert META_EXT+MAX_COUNT*32<=FTABLE_EXT and FTABLE_EXT+MAX_COUNT*16<=INNER
    if base%64 or not protected_start<=base or base+SIZE>protected_start+protected_size:
        raise ValueError('worker outside protected payload')
    if flash_offset%4096 or not 0x200000<=flash_offset or flash_offset+len(pack)+15>0x400000:
        raise ValueError('invalid flash pack region')
    blob=bytearray(SIZE)
    for i,(off,n,w,h,crc) in enumerate(records):
        struct.pack_into('<5I',blob,meta+i*32,flash_offset+off,n,w,i+1,crc)
        struct.pack_into('<4I',blob,ftable+i*16,0,n,w,i+1)
    for off,code in ((0,reader(base,count,protected_start,protected_size)),
                     (INNER,worker(base+INNER,SIZE-INNER,count,protected_start,protected_size,input_disjoint=True,
                                   max_power_state=max_power_state,rotate180=rotate180,table=ftable-INNER)),
                     (INNER+EVENT,event(base+INNER,count,menu_virtual,base,base+CONTROL,random_timing=random_timing,debounce_ms=debounce_ms))):
        blob[off:off+len(code)]=code
    struct.pack_into('<I',blob,INNER+STATE+16,0xffffffff)
    struct.pack_into('<I',blob,INNER+STATE+32,0x3295b)
    struct.pack_into('<I',blob,INNER+STATE+40,1)
    blob[INNER+LUT:INNER+LUT+256]=bytes((v*190+127)//255 for v in range(256))
    return bytes(blob)
