"""Execute resident flash-write guards offline; no USB or real flash calls."""
import struct, unittest
from mk1_native import CPU
from mk1_fast_flash import writer
from mk1_usb_flash import ERASE,PROGRAM,BEFORE,AFTER
ORIGIN=0x02300000; RESULT=0x02302000; SOURCE=0x02304000; STACK=0x0230f000

class GuardCPU(CPU):
    def __init__(self,program,off,seal=None,pointer=RESULT,length=8,corrupt=False):
        self.mem=bytearray(0x10000); self.hit=[]
        blob=writer(ORIGIN,RESULT,SOURCE,[0x23000,0x78000],program); self.mem[:len(blob)]=blob
        self.mem[RESULT-ORIGIN:RESULT-ORIGIN+8]=struct.pack('<II',off,off^0xffffffff if seal is None else seal)
        raw=BEFORE+bytes([123])*4096+AFTER
        self.mem[SOURCE-ORIGIN:SOURCE-ORIGIN+len(raw)]=raw
        if corrupt: self.mem[SOURCE-ORIGIN+7]^=1
        super().__init__([(ORIGIN,self.mem,True)]); self.r[1]=STACK; self.r[3]=pointer; self.r[4]=length
    def access(self,a,n,value=None):
        if value is None and n==4 and a in (ERASE,PROGRAM):
            self.hit.append((a,self.r[3:6])); self.r[11]=0; return 0x44004800
        return super().access(a,n,value)
    def result(self): return struct.unpack_from('<II',self.mem,RESULT-ORIGIN)

class Tests(unittest.TestCase):
    def test_only_exact_approved_sector_reaches_driver(self):
        for programming in (False,True):
            for offset in (0,0x1000,0x2fd000,0x77fff,0x78100,0x79000,0x400000,0xffffffff):
                c=GuardCPU(programming,offset); c.run(ORIGIN)
                self.assertFalse(c.hit); self.assertEqual(c.result(),(0xffffffff,0x1970)); self.assertEqual(c.r[1],STACK)
            for offset in (0x23000,0x78000):
                c=GuardCPU(programming,offset); c.run(ORIGIN)
                self.assertEqual(c.result(),(0,0x1970)); self.assertEqual(len(c.hit),1)
                self.assertEqual(c.hit[0][0],PROGRAM if programming else ERASE)
                self.assertEqual(c.hit[0][1][0],offset)
                if programming: self.assertEqual(c.hit[0][1][1:], [SOURCE+64,4096])
    def test_bad_request_or_source_is_refused(self):
        for programming in (False,True):
            for kwargs in ({'seal':0},{'pointer':RESULT+4},{'length':4},{'corrupt':True}):
                c=GuardCPU(programming,0x78000,**kwargs); c.run(ORIGIN)
                self.assertFalse(c.hit); self.assertEqual(c.result(),(0xffffffff,0x1970))
    def test_constructor_protects_boot_settings_resources(self):
        for bad in ([0],[0x2fd000],[0xc4000],[],[0x78000,0x78000]):
            with self.assertRaises(ValueError): writer(ORIGIN,RESULT,SOURCE,bad,False)
    def test_constructor_rejects_unbounded_or_overlapping_memory(self):
        for origin,result,source in ((0,RESULT,SOURCE),(ORIGIN,0, SOURCE),
                (ORIGIN,RESULT,0x027febc0),(ORIGIN,RESULT,ORIGIN),
                (ORIGIN,ORIGIN+4,SOURCE),(ORIGIN+4,RESULT,SOURCE),
                (ORIGIN,RESULT+1,SOURCE),(ORIGIN,RESULT,SOURCE+1)):
            with self.assertRaises(ValueError): writer(origin,result,source,[0x23000],True)
if __name__=='__main__': unittest.main()
