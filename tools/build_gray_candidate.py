#!/usr/bin/env python3
"""OFFLINE PARTIAL candidate: grayscale correction only. NOT ready to flash.

Reuses the existing 940-byte monochrome kernel region, replacing that kernel
with the already emulated Bayer4 implementation plus a grayscale trampoline.
No UI/key hooks or save-to-card suppression are installed by this builder.
"""
import argparse
import hashlib
import json
import struct
from pathlib import Path
from print_patch import START, END, make_kernel, make_lut
from analyze_firmware import EXPECTED_SHA256
from or1k_subset import Assembler

HOOK=0x4cf20
ENTRY=START+0x320
ORIGINAL=0xd7e1e7f8  # sw -8(r1),r28, replayed before returning to HOOK+4


def branch(source,target):
    delta=target-source
    if delta%4 or not -(1<<27)<=delta<(1<<27): raise ValueError('invalid branch')
    return struct.pack('<I',(delta//4)&0x3ffffff)


def build(original,endpoint=190):
    if hashlib.sha256(original).hexdigest()!=EXPECTED_SHA256: raise ValueError('original dump required')
    if original[HOOK:HOOK+4]!=struct.pack('<I',ORIGINAL): raise ValueError('hook mismatch')
    result=bytearray(original)
    result[START:END]=make_kernel(make_lut(),'bayer4')
    code=gray_arithmetic(endpoint)
    result[ENTRY:ENTRY+len(code)]=code
    result[HOOK:HOOK+4]=branch(HOOK,ENTRY)
    # Keep CLAHE unchanged: this artifact changes grayscale only in intent;
    # replacement of its occupied old dither region is explicitly reported.
    return bytes(result)


def gray_arithmetic(endpoint=190):
    if type(endpoint) is not int or not 1<=endpoint<=255: raise ValueError('endpoint outside 1..255')
    a=Assembler()
    # Preserve all original r3..r8 arguments. No calls, allocation or new state.
    # Only mode 0 is scaled; already-binary mode 1 must not be scaled again.
    a.immediate(0x2f,1,7,0); a.branch(4,'resume')
    a.immediate(0x29,12,4,65535); a.immediate(0x29,13,5,65535)
    a.alu(12,12,13,0x306)
    a.immediate(0x2f,0,12,0); a.branch(4,'resume')
    a.immediate(0x2a,15,3,0)
    a.label('pixel')
    a.immediate(0x23,13,15,0)
    a.immediate(0x27,11,0,endpoint); a.alu(13,13,11,0x306)
    a.immediate(0x27,13,13,127)
    a.immediate(0x27,11,0,255); a.alu(13,13,11,0x30a)
    a.emit(0x36<<26 | 15<<16 | 13<<11)
    a.immediate(0x27,15,15,1); a.immediate(0x27,12,12,-1)
    a.immediate(0x2f,1,12,0); a.branch(4,'pixel')
    a.label('resume'); a.emit(ORIGINAL)
    code=a.finish()+branch(ENTRY+len(a.words)*4,HOOK+4)
    if ENTRY+len(code)>END: raise ValueError('trampoline exceeds replaced function')
    return code


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('source',type=Path); p.add_argument('output',type=Path)
    p.add_argument('--white-endpoint',type=int,default=190)
    args=p.parse_args()
    report=args.output.with_suffix(args.output.suffix+'.json')
    if args.output.exists() or report.exists(): p.error('refusing overwrite')
    original=args.source.read_bytes(); result=build(original,args.white_endpoint)
    info={'status':'PARTIAL OFFLINE EXPERIMENT; DO NOT FLASH',
          'original_sha256':EXPECTED_SHA256,'sha256':hashlib.sha256(result).hexdigest(),
          'white_endpoint':args.white_endpoint,'grayscale_formula':'(Y * endpoint + 127) // 255',
          'ranges':[[START,END],[HOOK,HOOK+4]],'trampoline':ENTRY,
          'changed_bytes':sum(a!=b for a,b in zip(original,result)),
          'limitations':['Not boot/checksum/hardware validated.',
             'Existing mode-1 dither kernel is replaced with Bayer4 to reclaim bounded code space; CLAHE remains.',
             'Button mapping, selectable algorithms, live preview and no-card-write policy are NOT installed.',
             'This file is NOT the requested complete firmware.']}
    with args.output.open('xb') as f: f.write(result)
    with report.open('x') as f: f.write(json.dumps(info,indent=2)+'\n')
    print(json.dumps(info,indent=2))

if __name__=='__main__': main()
