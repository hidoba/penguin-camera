#!/usr/bin/env python3
"""Build an EXPERIMENTAL offline tone-curve + threshold/Bayer4 firmware patch.

Replaces the existing monochrome preprocessing kernel, not heater/motor code.
No USB/serial access. This does NOT validate boot checksums or hardware behavior.
"""
import argparse
import hashlib
import json
import math
import struct
from pathlib import Path
from analyze_firmware import EXPECTED_SHA256
from or1k_subset import Assembler

START, END = 0x22d20, 0x230cc
RAM_BIAS = 0x02000000 - 0x2400
CLAHE_CALL = 0x4d6c8
BAYER = (0,8,2,10,12,4,14,6,3,11,1,9,15,7,13,5)


def make_lut(gamma=1.0, points=None):
    if not math.isfinite(gamma) or gamma <= 0:
        raise ValueError('gamma must be finite and > 0')
    if points is None:
        return bytes(round(255*(i/255)**gamma) for i in range(256))
    if gamma != 1.0: raise ValueError('choose gamma OR control points')
    if len(points) < 2 or points[0][0] != 0 or points[-1][0] != 255:
        raise ValueError('curve must start at x=0 and end at x=255')
    if any(len(p) != 2 or any(type(v) is not int or not 0 <= v <= 255 for v in p) for p in points):
        raise ValueError('curve points must be integer [x,y] pairs in 0..255')
    if any(a[0] >= b[0] for a,b in zip(points, points[1:])):
        raise ValueError('curve x coordinates must strictly increase')
    lut = []
    segment = 0
    for x in range(256):
        while x > points[segment+1][0]: segment += 1
        (x0,y0), (x1,y1) = points[segment:segment+2]
        lut.append(round(y0 + (y1-y0)*(x-x0)/(x1-x0)))
    return bytes(lut)


def make_kernel(lut, mode):
    if len(lut) != 256: raise ValueError('LUT must have 256 entries')
    if mode not in ('threshold', 'bayer4'): raise ValueError('unsupported mode')
    # LUT is at fixed +0x200, comfortably beyond the generated instructions.
    lut_address = RAM_BIAS + START + 0x200
    matrix_address = lut_address + 256
    a = Assembler()
    a.immediate(0x29, 4, 4, 65535)
    a.immediate(0x29, 5, 5, 65535)
    a.immediate(0x2f, 0, 4, 0); a.branch(4, 'done')
    a.immediate(0x2f, 0, 5, 0); a.branch(4, 'done')
    a.immediate(6, 7, 0, lut_address >> 16)
    a.immediate(0x2a, 7, 7, lut_address)
    a.immediate(6, 8, 0, matrix_address >> 16)
    a.immediate(0x2a, 8, 8, matrix_address)
    a.immediate(0x27, 13, 0, 0)  # y
    a.label('row')
    a.immediate(0x27, 12, 0, 0)  # x
    a.label('pixel')
    a.immediate(0x23, 11, 3, 0)
    a.alu(11, 7, 11)
    a.immediate(0x23, 11, 11, 0)
    if mode == 'bayer4':
        a.immediate(0x29, 6, 13, 3)
        a.immediate(0x2e, 6, 6, 2)
        a.immediate(0x29, 15, 12, 3)
        a.alu(6, 6, 15)
        a.alu(6, 8, 6)
        a.immediate(0x23, 6, 6, 0)
    else:
        a.immediate(0x27, 6, 0, 128)
    a.emit(0x39 << 26 | 3 << 21 | 11 << 16 | 6 << 11)  # sfgeu
    a.immediate(0x27, 11, 0, 0)
    a.branch(3, 'store')
    a.immediate(0x27, 11, 0, 255)
    a.label('store')
    a.emit(0x36 << 26 | 3 << 16 | 11 << 11)  # sb 0(r3),r11
    a.immediate(0x27, 3, 3, 1)
    a.immediate(0x27, 12, 12, 1)
    a.emit(0x39 << 26 | 4 << 21 | 12 << 16 | 4 << 11)  # sfltu x,width
    a.branch(4, 'pixel')
    a.immediate(0x27, 13, 13, 1)
    a.emit(0x39 << 26 | 4 << 21 | 13 << 16 | 5 << 11)
    a.branch(4, 'row')
    a.label('done')
    a.immediate(0x27, 11, 0, 0)
    a.emit(0x44004800)  # jr r9, no delay slot
    code = a.finish()
    if len(code) > 0x200: raise ValueError('code overlaps LUT')
    result = code + b'\0'*(0x200-len(code)) + lut + bytes(8+16*v for v in BAYER)
    if len(result) > END-START: raise ValueError('kernel exceeds existing function')
    return result + b'\0'*(END-START-len(result))


def patch(data, lut, mode, keep_clahe=False):
    if hashlib.sha256(data).hexdigest() != EXPECTED_SHA256:
        raise ValueError('requires verified ORIGINAL firmware')
    if data[CLAHE_CALL:CLAHE_CALL+4] != struct.pack('<I', 0x07ff53a1):
        raise ValueError('CLAHE callsite mismatch')
    result = bytearray(data)
    result[START:END] = make_kernel(lut, mode)
    if not keep_clahe:
        # Pretend CLAHE returned success, letting our custom curve act directly
        # on decoded luminance without preceding automatic local enhancement.
        result[CLAHE_CALL:CLAHE_CALL+4] = struct.pack('<I', 0x9d600000)
    return bytes(result)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source', type=Path)
    p.add_argument('output', type=Path)
    p.add_argument('--mode', choices=('threshold','bayer4'), required=True)
    p.add_argument('--gamma', type=float, default=1.0, help='y=255*(x/255)^gamma; <1 brightens')
    p.add_argument('--points', type=json.loads, help='piecewise-linear curve, e.g. [[0,0],[128,160],[255,255]]')
    p.add_argument('--keep-clahe', action='store_true')
    args = p.parse_args()
    if args.output.exists(): p.error('output exists; choose a new filename')
    lut = make_lut(args.gamma, args.points)
    original = args.source.read_bytes()
    result = patch(original, lut, args.mode, args.keep_clahe)
    report = dict(status='EXPERIMENTAL, EMULATION-TESTED ONLY; NOT HARDWARE-VALIDATED',
                  source_sha256=EXPECTED_SHA256, output_sha256=hashlib.sha256(result).hexdigest(),
                  mode=args.mode, gamma=args.gamma, points=args.points, keep_clahe=args.keep_clahe,
                  function_file_range=[START,END], function_ram_address=RAM_BIAS+START,
                  clahe_callsite=CLAHE_CALL, changed_bytes=sum(a!=b for a,b in zip(original,result)),
                  lut=list(lut), limitations=['Only existing monochrome preprocessing path is patched.',
                  'Mode selection in UI has not been extended.', 'Boot integrity checks remain unverified.',
                  'Actual print density, timing, and runtime ABI require device validation.'])
    report_path = args.output.with_suffix(args.output.suffix+'.json')
    if report_path.exists(): p.error('report path exists')
    with args.output.open('xb') as f: f.write(result)
    with report_path.open('x') as f: f.write(json.dumps(report,indent=2)+'\n')
    print(f'Created offline candidate {args.output}; NOT validated for flashing.')
    print(f"{report['changed_bytes']} changed bytes; SHA-256 {report['output_sha256']}")


if __name__ == '__main__': main()
