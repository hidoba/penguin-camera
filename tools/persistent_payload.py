"""Relocatable effects payload for future cold-boot integration, OFFLINE ONLY.

Do not mistake this for a boot loader: a target loader must allocate owned RAM,
read/verify the package, relocate it, validate original hook words, synchronize
caches and only then install hooks. Current live frame bindings still need work.
"""
import hashlib
import json
import struct
from pathlib import Path
from persistent_effects_patch import build, SIZE, NAMES
from ui_trace_patch import BIAS
from or1k_subset import signed

BASE=0x02090000
PAIR=1
BRANCH=2
ABSOLUTE=3
HOOK_POINTER=1
HOOK_BRANCH=2
HEADER=struct.Struct('<4s7I')
RELOC=struct.Struct('<II')
HOOK=struct.Struct('<IIII')

def sha(data):return hashlib.sha256(data).hexdigest()
def word(blob,off):return struct.unpack_from('<I',blob,off)[0]
def setword(blob,off,value):struct.pack_into('<I',blob,off,value&0xffffffff)

def relocation_map(blob,size=SIZE):
    # Classify only address-dependent words, and verify the whole result
    # against independently rebuilt code at multiple bases below. This is
    # NOT a general binary disassembler or a scan-and-rewrite device loader.
    records=[];off=0
    while off<len(blob):
        w=word(blob,off)
        if w>>26==6 and off+8<=len(blob):
            second=word(blob,off+4);reg=(w>>21)&31
            if second>>26==0x2a and (second>>21)&31==reg and (second>>16)&31==reg:
                address=((w&65535)<<16)|(second&65535)
                if BASE<=address<=BASE+size:
                    records.append((PAIR,off));off+=8;continue
        # A pointer into the payload has opcode bits 0 and would otherwise be
        # tested (and dropped) as a branch first; real external branches encode
        # a negative 26-bit offset and never fall in this range. Every record is
        # still verified against fresh builds at several bases in package().
        if BASE<=w<=BASE+size:
            records.append((ABSOLUTE,off))
        elif w>>26 in (0,1,3,4):
            target=BASE+off+signed(w&0x3ffffff,26)*4
            # Only known stock application range; zero/data words must not
            # accidentally qualify as external code references.
            if BIAS+0x2400<=target<BIAS+0x83e00:
                records.append((BRANCH,off))
        off+=4
    return records

def relocate(blob,records,base,size=SIZE):
    if len(blob)!=size or base%64 or not BASE<=base<=0x02200000-size:raise ValueError('invalid allocation')
    out=bytearray(blob);delta=base-BASE;seen=set()
    for kind,off in records:
        width=8 if kind==PAIR else 4
        if kind not in (PAIR,BRANCH,ABSOLUTE) or off%4 or off<0 or off+width>len(out):
            raise ValueError('bad relocation record')
        if any(p in seen for p in range(off,off+width,4)):raise ValueError('overlapping relocation')
        seen.update(range(off,off+width,4));w=word(out,off)
        if kind==PAIR:
            second=word(out,off+4);reg=(w>>21)&31
            if w>>26!=6 or second>>26!=0x2a or (second>>21)&31!=reg or (second>>16)&31!=reg:
                raise ValueError('bad movhi/ori relocation')
            address=((w&65535)<<16)|(second&65535)
            if not BASE<=address<=BASE+size:raise ValueError('pair outside allocation')
            address+=delta
            setword(out,off,(w&0xffff0000)|(address>>16))
            setword(out,off+4,(second&0xffff0000)|(address&65535))
        elif kind==BRANCH:
            if w>>26 not in (0,1,3,4):raise ValueError('not a branch')
            target=BASE+off+signed(w&0x3ffffff,26)*4
            if not BIAS+0x2400<=target<BIAS+0x83e00:raise ValueError('not an external stock branch')
            relative=(target-(base+off))//4
            if not -(1<<25)<=relative<(1<<25):raise ValueError('branch out of range')
            setword(out,off,(w&0xfc000000)|(relative&0x3ffffff))
        else:
            if not BASE<=w<=BASE+size:raise ValueError('pointer outside allocation')
            setword(out,off,w+delta)
    return bytes(out)

def hook_records(patches,size=SIZE):
    records=[]
    for p in patches:
        address=p['address'];old=int.from_bytes(bytes.fromhex(p['original']),'little')
        new=int.from_bytes(bytes.fromhex(p['replacement']),'little');kind=0
        if BASE<=new<BASE+size:kind=HOOK_POINTER
        elif new>>26 in (0,1):
            target=address+signed(new&0x3ffffff,26)*4
            if BASE<=target<BASE+size:kind=HOOK_BRANCH
        records.append((address,old,new,kind))
    return records

def relocate_hooks(records,base,size=SIZE):
    if base%64 or not BASE<=base<=0x02200000-size:raise ValueError('invalid allocation')
    result=[];delta=base-BASE
    for address,old,new,kind in records:
        if not BIAS+0x2400<=address<BIAS+0x83e00 or address%4:raise ValueError('bad hook destination')
        if kind==HOOK_POINTER:
            if not BASE<=new<BASE+size:raise ValueError('bad hook pointer')
            new+=delta
        elif kind==HOOK_BRANCH:
            target=address+signed(new&0x3ffffff,26)*4
            if new>>26 not in (0,1) or not BASE<=target<BASE+size:raise ValueError('bad hook branch')
            new=(new&0xfc000000)|(((target+delta-address)//4)&0x3ffffff)
        elif kind!=0:raise ValueError('bad hook kind')
        result.append((address,old,new))
    return result

def package(original,builder=build,size=SIZE):
    blob,patches=builder(BASE,original)
    if len(blob)!=size:raise ValueError('builder size mismatch')
    # A byte-difference oracle filters accidental branch-like words in data.
    nearby,_=builder(BASE+0x10040,original)
    changed={off for off in range(0,size,4) if word(blob,off)!=word(nearby,off)}
    records=[(kind,off) for kind,off in relocation_map(blob,size)
             if off in changed or (kind==PAIR and off+4 in changed)]
    hooks=hook_records(patches,size)
    for base in (BASE,BASE+64,BASE+0xffc0,BASE+0x10040,0x02134600,0x02200000-size):
        expected,expected_hooks=builder(base,original)
        if relocate(blob,records,base,size)!=expected:raise ValueError(f'unclassified relocation at {base:#x}')
        wanted=[(p['address'],int.from_bytes(bytes.fromhex(p['original']),'little'),
                 int.from_bytes(bytes.fromhex(p['replacement']),'little')) for p in expected_hooks]
        if relocate_hooks(hooks,base,size)!=wanted:raise ValueError('hook relocation mismatch')
    body=blob+b''.join(RELOC.pack(*r) for r in records)+b''.join(HOOK.pack(*h) for h in hooks)
    # SHA256 lives in the integration manifest; checksum is a corruption check,
    # not authentication. No executing loader is emitted here.
    import zlib
    payload=HEADER.pack(b'PGFX',1,BASE,size,len(records),len(hooks),len(body),zlib.crc32(body))+body
    return payload,{'modes':NAMES,'allocation_bytes':size,'link_base':hex(BASE),
        'relocation_count':len(records),'hook_count':len(hooks),'sha256':sha(payload),
        'relocation_validation':'byte-exact against fresh builds at six aligned bases',
        'release_blockers':['cold-boot loader not yet installed/validated',
            'menu display/navigation verified in RAM attempt 06; combined cold-boot integration pending',
            'Random penguin currently queues a request only; JPEG decode/print worker not connected',
            'preview still binds session-specific frame addresses',
            'cracked mode is the previously rejected approximation; not release-ready',
            '1024-axis image processing is host-tested only; stock decoder/driver long-image hardware test pending']}

def unpack(payload,expected_size=SIZE):
    import zlib
    if len(payload)<HEADER.size:raise ValueError('truncated payload')
    magic,version,base,size,nr,nh,length,crc=HEADER.unpack_from(payload)
    if (magic,version,base,size)!=(b'PGFX',1,BASE,expected_size):raise ValueError('wrong effects header')
    body=payload[HEADER.size:]
    if len(body)!=length or length!=size+nr*RELOC.size+nh*HOOK.size:raise ValueError('bad payload size')
    if zlib.crc32(body)!=crc:raise ValueError('effects CRC mismatch')
    records=[RELOC.unpack_from(body,size+i*RELOC.size) for i in range(nr)]
    pos=size+nr*RELOC.size
    hooks=[HOOK.unpack_from(body,pos+i*HOOK.size) for i in range(nh)]
    blob=body[:size]
    relocate(blob,records,BASE,size);relocate_hooks(hooks,BASE,size)
    return blob,records,hooks
