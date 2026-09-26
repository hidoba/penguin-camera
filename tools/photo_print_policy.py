"""Photo-only always-print entry guards, with bounded capture diagnostics.

Keeps the original shutter/capture handlers and their battery/paper checks.
Never invokes handlers by itself, saves images to storage, or alters dithering.
"""
import struct
from live_preview_patch import Code, BIAS
from build_gray_candidate import branch

SHUTTER=0x800
CAPTURE=0xa00
DIAGNOSTICS=0xf00
DIAGNOSTIC_SIZE=32
FLAG=0x02089342
REMAINING=0x0207e258
HOOKS=((0x805f4,0xb3e0,SHUTTER),(0x805fc,0xb9b4,CAPTURE))


def wrapper(base, capture=False):
    offset=CAPTURE if capture else SHUTTER
    origin=base+offset;target=BIAS+(0xb9b4 if capture else 0xb3e0)
    a=Code();imm=a.immediate
    # Match stock fresh-press semantics; releases/repeats keep stock behavior.
    imm(0x2f,1,4,1);a.branch(4,'fallback')
    a.const(11,0x02000000);a.compare(5,11,4);a.branch(4,'fallback')
    a.const(11,0x021ffffc);a.compare(5,11,2);a.branch(4,'fallback')
    imm(0x29,11,5,3);imm(0x2f,1,11,0);a.branch(4,'fallback')
    imm(0x21,11,5,0);imm(0x2f,1,11,0);a.branch(4,'fallback')
    imm(0x27,1,1,-16)
    for i,r in enumerate((9,12,13)):a.store(r,1,i*4)
    if capture:
        a.const(12,base+DIAGNOSTICS);imm(0x21,13,12,0)
        imm(0x27,13,13,1);a.store(13,12,0)
        for address,off in ((FLAG,12),(REMAINING,16)):
            a.const(11,address);imm(0x23,13,11,0);a.store(13,12,off)
        a.const(11,0x02085e9c);imm(0x21,13,11,0);a.store(13,12,28)
    a.const(12,FLAG);imm(0x27,13,0,1);a.store(13,12,op=0x36)
    a.call(origin,target)
    if capture:
        a.const(12,base+DIAGNOSTICS);a.store(11,12,8)
        imm(0x21,13,12,4);imm(0x27,13,13,1);a.store(13,12,4)
        # Keep r11 (original handler return) intact while reading final flags.
        a.const(13,FLAG);imm(0x23,13,13,0);a.store(13,12,20)
        a.const(13,REMAINING);imm(0x23,13,13,0);a.store(13,12,24)
    for i,r in enumerate((9,12,13)):imm(0x21,r,1,i*4)
    imm(0x27,1,1,16);a.emit(0x44004800)
    a.label('fallback')
    code=a.finish()+branch(origin+len(a.words)*4,target)
    if len(code)>0x200:raise ValueError('Print-policy wrapper overflow')
    return code


def install_parts(base,original,blob):
    if any(blob[SHUTTER:0x1000]):raise ValueError('Policy slots are occupied')
    patches=[]
    for off,stock,slot in HOOKS:
        expected=struct.pack('<I',BIAS+stock)
        if original[off:off+4]!=expected:raise ValueError('Photo callback mismatch')
        code=wrapper(base,slot==CAPTURE);blob[slot:slot+len(code)]=code
        patches.append({'address':BIAS+off,'original':expected.hex(),
                        'replacement':struct.pack('<I',base+slot).hex(),
                        'label':'always-print '+('capture' if slot==CAPTURE else 'shutter')})
    return patches
