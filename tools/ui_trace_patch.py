"""Bounded RAM-only UI diagnostics. Generated wrappers tail-call stock handlers.

No frame pixels are changed. Trace ring stores callback args/settings and two
52-byte descriptor snapshots. Physical button identities remain hypotheses.
"""
import struct
from or1k_subset import Assembler
from or1k_subset import branch

BIAS=0x02000000-0x2400
SIZE=0x5000
HEADER=0x2000
RECORDS=0x2100
CAPACITY=32
STRIDE=256
MAGIC=0x55495431
DISPATCH_HOOK=0x87d4
DISPATCH_SLOT=0x1800
FILTERED_SLOT=0x1c00
DISPATCH_ORIGINAL=0xbc0e0000  # sfeqi r14,0; flags must be replayed
# pointer-file-offset, event, original-handler-file-offset, contextual label
HOOKS=((0x80624,0x2e,0xb6cc,'photo toggle'),
       (0x8060c,0x1e,0xc1d4,'photo direction A'),
       (0x80614,0x20,0xbef4,'photo direction B'),
       (0x80ba0,0x2e,0xde44,'playback toggle'),
       (0x80bc8,0x1e,0xe60c,'playback direction A'),
       (0x80bd0,0x20,0xe7e0,'playback direction B'))

def wrapper(base,index,dispatch=False,key_only=False):
    _,event,handler,_=HOOKS[index]
    address=base+(FILTERED_SLOT if key_only else DISPATCH_SLOT if dispatch else index*0x400)
    a=Assembler()
    def imm(op,d,s,v): a.immediate(op,d,s,v)
    def const(reg,value): imm(6,reg,0,value>>16); imm(0x2a,reg,reg,value)
    def store(src,offset,dest=15):
        a.emit(0x35<<26 | ((offset&65535)>>11)<<21 | dest<<16 | src<<11 | (offset&2047))
    def valid(pointer,size,label):
        const(13,0x02000000)
        a.emit(0x39<<26 | 4<<21 | pointer<<16 | 13<<11); a.branch(4,label)
        const(13,0x02200000-size)
        a.emit(0x39<<26 | 2<<21 | pointer<<16 | 13<<11); a.branch(4,label)
        imm(0x29,13,pointer,3); imm(0x2f,1,13,0); a.branch(4,label)
    for i,r in enumerate((11,12,13,15),1): store(r,-4*i,1)
    imm(0x27,1,1,-16)
    if key_only:
        # 0x09/0x0b are periodic background events observed on-device. Keep
        # only the physical-control range 0x1c..0x30; r2 is event * 4.
        imm(0x2f,5,2,0x1b*4); a.branch(4,'restore')
        imm(0x2f,2,2,0x30*4); a.branch(4,'restore')
    const(11,base+HEADER)
    imm(0x21,12,11,4)
    imm(0x29,13,12,CAPACITY-1); imm(0x2e,13,13,8)
    const(15,base+RECORDS); a.alu(15,15,13)
    imm(0x27,12,12,1); store(12,0)
    if dispatch:
        # At 0x87d4 r2 is the validated event index * 4, r14 the primary
        # callback (possibly null), and the original stack +4 holds key subtype.
        imm(0x2e,13,2,0x42)
        const(12,0x10000); a.alu(13,13,12,4); store(13,4)
        store(14,8)
        imm(0x21,13,1,20); store(13,12)
        const(12,0x02085d04); a.alu(12,12,2)
        imm(0x21,13,12,0); store(13,60)
    else:
        imm(0x27,13,0,event); store(13,4)
        const(13,BIAS+handler); store(13,8)
        imm(0x27,13,0,-1); store(13,12)
    for i,r in enumerate(range(3,9)): store(r,16+4*i)
    if not dispatch:
        imm(0x2f,1,4,1); a.branch(4,'payload_done')
        valid(5,4,'payload_done')
        imm(0x21,12,5,0); store(12,12)
        a.label('payload_done')
    const(11,0x020892c8)
    imm(0x21,12,11,0); store(12,48)
    imm(0x21,12,11,120); store(12,52)
    imm(0x27,12,0,0); store(12,56)
    for j,global_pointer in enumerate((0x020867a0,0x02086988)):
        const(11,global_pointer); imm(0x21,11,11,0)
        store(11,40+j*4)
        valid(11,52,f'desc{j}_done')
        for offset in range(0,52,4):
            imm(0x21,12,11,offset); store(12,64+j*64+offset)
        imm(0x21,12,15,56); imm(0x2a,12,12,1<<j); store(12,56)
        a.label(f'desc{j}_done')
    # Commit count after snapshot; no calls or interrupt-context hooks here.
    const(11,base+HEADER)
    imm(0x21,12,15,0); store(12,4,11)
    a.label('restore')
    imm(0x27,1,1,16)
    for i,r in enumerate((11,12,13,15),1): imm(0x21,r,1,-4*i)
    if dispatch: a.emit(DISPATCH_ORIGINAL)
    code=a.finish()
    code+=branch(address+len(code),BIAS+(DISPATCH_HOOK+4 if dispatch else handler))
    if len(code)>0x400: raise ValueError('wrapper slot overflow')
    return code

def build(base,original):
    if not 0x02090000<=base<=0x02200000-SIZE or base%64: raise ValueError('unapproved allocation')
    blob=bytearray(SIZE)
    patches=[]
    for i,(off,event,target,label) in enumerate(HOOKS):
        expected=struct.pack('<I',BIAS+target)
        if original[off:off+4]!=expected or struct.unpack_from('<I',original,off-4)[0]!=event:
            raise ValueError(f'original callback table mismatch at {off:#x}')
        code=wrapper(base,i); blob[i*0x400:i*0x400+len(code)]=code
        patches.append({'address':BIAS+off,'original':expected.hex(),
                        'replacement':struct.pack('<I',base+i*0x400).hex(),'label':label})
    struct.pack_into('<IIII',blob,HEADER,MAGIC,0,CAPACITY,STRIDE)
    return bytes(blob),patches

def build_dispatch(base,original):
    blob,patches=build(base,original)
    expected=struct.pack('<I',DISPATCH_ORIGINAL)
    if original[DISPATCH_HOOK:DISPATCH_HOOK+4]!=expected:
        raise ValueError('dispatcher instruction mismatch')
    blob=bytearray(blob)
    code=wrapper(base,0,dispatch=True)
    blob[DISPATCH_SLOT:DISPATCH_SLOT+len(code)]=code
    patches.append({'address':BIAS+DISPATCH_HOOK,'original':expected.hex(),
                    'replacement':branch(BIAS+DISPATCH_HOOK,base+DISPATCH_SLOT).hex(),
                    'label':'common queued-key dispatcher'})
    return bytes(blob),patches

def build_filtered_dispatch(base,original):
    blob,patches=build_dispatch(base,original)
    blob=bytearray(blob)
    code=wrapper(base,0,dispatch=True,key_only=True)
    blob[FILTERED_SLOT:FILTERED_SLOT+len(code)]=code
    patches[-1]['replacement']=branch(BIAS+DISPATCH_HOOK,base+FILTERED_SLOT).hex()
    return bytes(blob),patches

