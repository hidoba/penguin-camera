"""Offline native cold-boot loader generator; NOT placed in firmware yet.

Precondition: stock mode/event dispatch has NOT started. Hook publication is
not atomic for a running application. This is deliberately not a USB installer.
The trusted loader embeds the exact PGFX header/CRC and table counts for one
build; arbitrary packages are not accepted. CRC detects corruption, not attacks.
"""
import struct
from live_preview_patch import Code
from persistent_payload import unpack, HEADER, BASE, SIZE, PAIR, BRANCH
from ui_trace_patch import BIAS

ALLOC=BIAS+0x3ace0
FREE=BIAS+0x3adb0
READ=BIAS+0x39528
INVALIDATE=BIAS+0x2980c
FLUSH=BIAS+0x29790
ICACHE=BIAS+0x2974c

def build_loader(payload,origin,flash_offset=0x200000,size=SIZE,rehearsal=None,usb_recovery=False):
    """Return native instructions. r11=0 success/8 failure; others preserved.

    Retains successful allocation. Frees a verified owned allocation on failure.
    Invalid allocator returns fail without trying to free an untrusted pointer.
    No SPI erase/program calls. Caller must provide a verified loadable code site.
    Optional rehearsal=(mirror,record) redirects ONLY hook preflight/publication
    to a caller-owned scratch mirror and records the allocator's return. This
    diagnostic may run in owned heap code; it never publishes live hooks.
    usb_recovery skips the extension before allocation/SPI access when stock
    startup has selected USB process 2. It preserves the normal USB mode path.
    """
    blob,relocs,hooks=unpack(payload,expected_size=size)
    if not hooks:raise ValueError('no hooks')
    if rehearsal is None:
        if origin%4 or not 0x02000000<=origin<0x02080000:raise ValueError('bad loader origin')
    else:
        mirror,record=rehearsal
        if origin%64 or not BASE<=origin<=0x02200000-4096:
            raise ValueError('bad rehearsal origin')
        if mirror!=origin+2048 or record!=origin+2304 or len(hooks)>64:
            raise ValueError('rehearsal requires fixed disjoint code/mirror/record layout')
    rounded_read=(len(payload)+15)&~15
    if flash_offset%4096 or not 0x200000<=flash_offset<=0x400000-rounded_read:
        raise ValueError('bad flash region')
    a=Code();imm=a.immediate
    saved=tuple(r for r in range(2,31) if r!=11)
    imm(0x27,1,1,-112)
    for i,r in enumerate(saved):a.store(r,1,i*4)
    if usb_recovery:
        a.const(12,0x020892cb);imm(0x23,12,12,0)
        imm(0x2f,0,12,2);a.branch(4,'reject')
    # Leave 32 bytes before the header so code after its 32 bytes is 64-aligned.
    allocation_bytes=rounded_read+32  # stock SPI DMA rounds transfer up to 16
    a.const(3,allocation_bytes);imm(0x27,4,0,64);a.call(origin,ALLOC)
    imm(0x2a,18,11,0)
    if rehearsal is not None:
        a.const(12,record);a.store(18,12)
    a.const(12,BASE);a.compare(18,12,4);a.branch(4,'reject')
    a.const(12,0x02200000-allocation_bytes);a.compare(18,12,2);a.branch(4,'reject')
    imm(0x29,12,18,63);imm(0x2f,1,12,0);a.branch(4,'reject')
    a.const(12,allocation_bytes);a.alu(12,18,12);a.compare(12,1,2);a.branch(4,'reject')
    imm(0x27,14,18,32);imm(0x27,20,18,64)
    # SPI helper has no reliable failure status. Exact header + full body CRC
    # are mandatory; invalidate before inspecting a possible DMA destination.
    a.const(3,flash_offset);imm(0x2a,4,14,0);a.const(5,len(payload));a.call(origin,READ)
    imm(0x2a,3,14,0);a.const(4,len(payload));a.call(origin,INVALIDATE)
    # A short table loop keeps the loader within the old updater's code budget.
    # Table address is fixed after assembly; it is not a runtime relocation.
    header_address_fixup=len(a.words)
    a.const(24,0);imm(0x27,26,0,8)
    a.label('header_word');imm(0x21,12,14,0);imm(0x21,13,24,0)
    a.compare(12,13);a.branch(4,'free_reject')
    imm(0x27,14,14,4);imm(0x27,24,24,4);imm(0x27,26,26,-1)
    imm(0x2f,1,26,0);a.branch(4,'header_word')
    # Standard reflected CRC32, byte-at-a-time, bounded by trusted build length.
    imm(0x2a,24,20,0);a.const(26,len(payload)-HEADER.size)
    if usb_recovery:imm(0x27,28,0,-1)  # one word shorter, exactly the same constant
    else:a.const(28,0xffffffff)
    a.const(30,0xedb88320)
    a.label('crc_byte');imm(0x23,12,24,0);a.alu(28,28,12,5);imm(0x27,13,0,8)
    a.label('crc_bit');imm(0x29,12,28,1);imm(0x2e,28,28,0x41)
    imm(0x2f,0,12,0);a.branch(4,'crc_next');a.alu(28,28,30,5)
    a.label('crc_next');imm(0x27,13,13,-1);imm(0x2f,1,13,0);a.branch(4,'crc_bit')
    imm(0x27,24,24,1);imm(0x27,26,26,-1);imm(0x2f,1,26,0);a.branch(4,'crc_byte')
    if usb_recovery:imm(0x27,12,0,-1)
    else:a.const(12,0xffffffff)
    a.alu(28,28,12,5)
    a.const(12,struct.unpack_from('<I',payload,28)[0]);a.compare(28,12);a.branch(4,'free_reject')
    # All live original hooks must match before any hook is touched. Tables
    # below are trusted only after CRC equals this exact build's embedded CRC.
    a.const(24,size+len(relocs)*8);a.alu(24,20,24);imm(0x27,26,0,len(hooks))
    a.label('preflight');imm(0x21,12,24,0)
    if rehearsal is not None:
        a.const(12,mirror+len(hooks)*4);imm(0x2e,13,26,2);a.alu(12,12,13,2)
    imm(0x21,13,24,4);imm(0x21,15,12,0)
    a.compare(13,15);a.branch(4,'free_reject')
    imm(0x27,24,24,16);imm(0x27,26,26,-1);imm(0x2f,1,26,0);a.branch(4,'preflight')
    a.const(12,BASE);a.alu(22,20,12,2);a.const(24,size);a.alu(24,20,24)
    imm(0x27,26,0,len(relocs));imm(0x2f,0,26,0);a.branch(4,'relocated')
    a.label('reloc');imm(0x21,12,24,0);imm(0x21,13,24,4);a.alu(13,20,13)
    imm(0x21,15,13,0);imm(0x2f,1,12,PAIR);a.branch(4,'not_pair')
    imm(0x21,16,13,4);imm(0x2e,17,15,16);imm(0x29,12,16,65535)
    a.alu(17,17,12,4);a.alu(17,17,22)
    a.const(12,0xffff0000);a.alu(15,15,12,3);a.alu(16,16,12,3)
    imm(0x2e,12,17,0x50);a.alu(15,15,12,4)
    imm(0x29,17,17,65535);a.alu(16,16,17,4);a.store(16,13,4);a.branch(0,'reloc_store')
    a.label('not_pair');imm(0x2f,1,12,BRANCH);a.branch(4,'absolute')
    a.const(12,0xfc000000);a.alu(16,15,12,3)
    imm(0x2e,12,22,0x42);a.alu(15,15,12,2);a.const(12,0x3ffffff)
    a.alu(15,15,12,3);a.alu(15,15,16,4);a.branch(0,'reloc_store')
    a.label('absolute');a.alu(15,15,22)
    a.label('reloc_store');a.store(15,13);imm(0x27,24,24,8)
    imm(0x27,26,26,-1);imm(0x2f,1,26,0);a.branch(4,'reloc')
    a.label('relocated')
    imm(0x2a,3,20,0);a.const(4,size);a.call(origin,FLUSH);a.call(origin,ICACHE)
    # No dispatcher is running at the intended boot call site. Publishing
    # these hooks into a live camera would require separate synchronization.
    a.const(24,size+len(relocs)*8);a.alu(24,20,24);imm(0x27,26,0,len(hooks))
    a.label('hook');imm(0x21,28,24,0);imm(0x21,15,24,8);imm(0x21,12,24,12)
    if rehearsal is not None:
        a.const(28,mirror+len(hooks)*4);imm(0x2e,13,26,2);a.alu(28,28,13,2)
    imm(0x2f,1,12,1);a.branch(4,'not_pointer');a.alu(15,15,22);a.branch(0,'hook_store')
    a.label('not_pointer');imm(0x2f,1,12,2);a.branch(4,'hook_store')
    a.const(12,0xfc000000);a.alu(16,15,12,3)
    imm(0x2e,12,22,0x42);a.alu(15,15,12);a.const(12,0x3ffffff)
    a.alu(15,15,12,3);a.alu(15,15,16,4)
    a.label('hook_store');a.store(15,28)
    imm(0x2a,3,28,0);imm(0x27,4,0,4);a.call(origin,FLUSH)
    imm(0x27,24,24,16);imm(0x27,26,26,-1);imm(0x2f,1,26,0);a.branch(4,'hook')
    a.call(origin,ICACHE);imm(0x27,11,0,0);a.branch(0,'restore')
    a.label('free_reject');imm(0x2a,3,18,0);a.call(origin,FREE)
    a.label('reject');imm(0x27,11,0,8)
    a.label('restore')
    for i,r in enumerate(saved):imm(0x21,r,1,i*4)
    imm(0x27,1,1,112);a.emit(0x44004800)
    code=bytearray(a.finish());table_address=origin+len(code)
    for i,value in enumerate((table_address>>16,table_address&65535)):
        pos=(header_address_fixup+i)*4
        word=struct.unpack_from('<I',code,pos)[0]
        struct.pack_into('<I',code,pos,(word&0xffff0000)|value)
    return bytes(code)+payload[:HEADER.size]
