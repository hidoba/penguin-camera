#!/usr/bin/env python3
"""Adapted from penguin tools/trace_boot_header.py for MK1 calibration/layout.
Offline trace of the captured ROM's header parser, NOT a full boot emulator.

Executes the real parser and inline header initializer. Models SPI reads as
copies from the supplied flash image, special registers as a dictionary, and
stubs the unchanged boot-stub calibration and two final hardware/cache cleanup
calls. Deliberately supplies a wrong
SPI CRC to test the actual rejection branch. Never accesses USB/hardware.
"""
import argparse
import json
from pathlib import Path
import struct

from penguin_port.or1k_subset import CPU
import hashlib
ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_SHA = '33ac2db5716dacf06b60f97c5efbb2b2b77d820fe2b13e58c1eeb4379094fab5'   # stock flash dump
ROM_SHA = '62d4dabeeca22efddc11ea0fa95a5a0823e1897e35659c9b5ae0bc47f4205eef'        # SoC boot ROM read over USB
def sha(data): return hashlib.sha256(data).hexdigest()

READ=0x1f0000
CLEANUP=0x1f0004
STATE=0x4f74


class HeaderCPU(CPU):
    def __init__(self,flash,rom,crc=0xffff):
        self.flash=flash;self.rom=rom;self.spr={0x9260:crc}
        self.reads=[];self.cleanup=[];self.accepted=False
        super().__init__([(0,bytearray(0x2400),True),
                          (0x4000,bytearray(0x2000),True),
                          (0x02000000,bytearray(0x100000),True),
                          (0x100000,bytearray(rom),False)])
        self.r[1]=0x5f00;self.r[3]=READ;self.r[4]=CLEANUP
        # Exact SPI boot setup at ROM 1014d8/1014f0.
        super().access(STATE+20,4,0x8000)

    def access(self,address,size,value=None):
        if value is None and size==4:
            if address==READ:
                target,sector,count=self.r[3:6]
                data=self.flash[sector*512:(sector+count)*512]
                if len(data)!=count*512:raise ValueError('Modeled SPI read exceeds flash')
                for base,region,writable in self.regions:
                    off=target-base
                    if writable and 0<=off and off+len(data)<=len(region):
                        region[off:off+len(data)]=data;break
                else:raise ValueError('Modeled SPI destination unmapped')
                self.reads.append({'destination':hex(target),'offset':hex(sector*512),'bytes':len(data)})
                self.r[11]=0
                return 0x44004800  # return to caller
            if address in (CLEANUP,0x11b0,0x104400,0x103ae4):
                self.cleanup.append(hex(address));return 0x44004800
            if address==0x100728:
                self.accepted=True
                self.entry=self.r[2]
            word=super().access(address,size)
            if word>>26==0x12:  # no-delay jalr -> equivalent relative jal
                target=self.r[(word>>11)&31];delta=target-address
                if delta%4 or not -(1<<27)<=delta<(1<<27):raise ValueError('Trace jalr range')
                return (1<<26)|((delta//4)&0x3ffffff)
            if word>>26==0x2d:
                spr=self.r[(word>>16)&31]|(word&65535)
                self.r[(word>>21)&31]=self.spr.get(spr,0)
                return 0x14000000
            if word>>26==0x30:
                spr=self.r[(word>>16)&31]|((word>>21)&31)<<11|(word&2047)
                self.spr[spr]=self.r[(word>>11)&31]
                return 0x14000000
            return word
        return super().access(address,size,value)


def trace(flash,rom):
    cpu=HeaderCPU(flash,rom)
    cpu.run(0x100478,stop=0,limit=100000)
    return {'parser_accepted':cpu.accepted,'entry':hex(cpu.entry) if cpu.accepted else None,
            'reads':cpu.reads,'final_SPI_flags':hex(cpu.access(STATE+21,1)),
            'header_flags':hex(flash[10]),'expected_CRC_low16':hex(int.from_bytes(flash[0x30:0x32],'little')),
            'modeled_SPI_CRC':hex(cpu.spr[0x9260]),'instructions':cpu.steps,
            'stubbed_cleanup_calls':cpu.cleanup,
            'limitations':'SPI DMA and SPR hardware modeled; unchanged boot-stub calibration at 0x11b0 and final hardware/cache cleanup stubbed; application not executed. Not a cold-boot validation.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--rom',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():parser.error('Output exists')
    original=(ROOT/'flash_read1.bin').read_bytes()
    rom=args.rom.read_bytes()
    candidate=args.candidate.read_bytes()
    if sha(original)!=ORIGINAL_SHA or sha(rom)!=ROM_SHA:raise ValueError('Reference changed')
    if len(candidate)!=len(original) or candidate[:0x2600]!=original[:0x2600]:
        raise ValueError('Candidate must preserve the complete boot-header/stub area')
    report={'original':trace(original,rom),'candidate':trace(candidate,rom),'candidate_sha256':sha(candidate)}
    # Positive control: make SPI flags signed-negative, retaining the wrong CRC.
    required=bytearray(original);required[0x39]|=128
    report['CRC_required_negative_control']=trace(required,rom)
    if not all(report[k]['parser_accepted'] for k in ('original','candidate')) or report['CRC_required_negative_control']['parser_accepted']:
        raise ValueError('Header acceptance/control trace failed')
    args.output.mkdir(parents=True)
    (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
