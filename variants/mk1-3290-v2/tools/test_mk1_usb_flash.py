"""Offline native bridge and write-policy checks; never open hardware."""
import struct
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from mk1_usb_flash import Code, bridge, guard, images, phases, SETTINGS
from penguin_port.or1k_subset import CPU

class NativeTests(unittest.TestCase):
    origin=0x02300000
    def run_guard(self,bounds,args):
        mem=bytearray(8192); target=self.origin+2048; record=self.origin+4096
        blob=guard(self.origin,target,bounds);mem[:len(blob)]=blob
        c=Code(target);c.const(11,record);c.emit(0xd40b1800);c.emit(0xd40b2004);c.emit(0xd40b2808);c.emit(0x9d600000);c.emit(0x44004800)
        mem[2048:2048+len(c.finish())]=c.finish()
        cpu=CPU([(self.origin,mem,True)]);cpu.r[3:3+len(args)]=args;cpu.run(self.origin)
        return cpu.r[11],struct.unpack_from('<3I',mem,4096)
    def test_exact_sector_erase(self):
        for off in [0xd1000,0x140000,0x2fe000,0x3bd000]:
            self.assertEqual(self.run_guard([off],[off]),(0,(off,0,0)))
            for bad in [0,off+1,off-4096,SETTINGS,0x400000]:
                self.assertEqual(self.run_guard([off],[bad]),(0xffffffff,(0,0,0)))
    def test_exact_program_arguments(self):
        bounds=[0x300000,self.origin+64,4096]
        self.assertEqual(self.run_guard(bounds,bounds),(0,tuple(bounds)))
        for i in range(3):
            for delta in [-1,1,256]:
                args=bounds.copy();args[i]+=delta
                self.assertEqual(self.run_guard(bounds,args),(0xffffffff,(0,0,0)))
    def test_call_bridge_returns_and_restores_stack(self):
        mem=bytearray(8192);result=self.origin+4096;target=self.origin+2048
        blob=bridge(target,[0x12345678,0x02000040,65536],self.origin,result)
        mem[:len(blob)]=blob;struct.pack_into('<2I',mem,2048,0x9d607654,0x44004800)
        cpu=CPU([(self.origin,mem,True)]);cpu.r[1]=self.origin+8000;cpu.run(self.origin)
        self.assertEqual(cpu.r[1],self.origin+8000)
        self.assertEqual(struct.unpack_from('<2I',mem,4096),(0x7654,0x1970))
    def test_exact_manifest_and_protected_regions(self):
        stock,target,changed=images();groups=phases(changed)
        self.assertEqual([len(v) for v in groups.values()],[190,49,1])
        self.assertEqual([a for v in groups.values() for a in v][-1],0xd1000)
        self.assertEqual(sorted(a for v in groups.values() for a in v),changed)
        self.assertTrue(all(a>=0xc4000 and a!=SETTINGS for a in changed))
        self.assertEqual(stock[:0xc4000],target[:0xc4000])
        self.assertEqual(stock[SETTINGS:SETTINGS+4096],target[SETTINGS:SETTINGS+4096])
if __name__=='__main__': unittest.main()
