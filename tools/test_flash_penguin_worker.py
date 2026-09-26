import struct
import unittest
from PIL import Image
from prepare_release_artwork import encode_jpeg
from prepare_print_penguins import pack,validate
from flash_penguin_worker import build,INNER,CONTROL,SIZE,META,FTABLE,FTABLE_EXT,layout
from random_penguin_ram import TABLE,STATE
from or1k_subset import CPU
from ui_trace_patch import BIAS

BASE=0x02134000
BUFFER=0x02150000
FLASH=0x240000


class FlashWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.jpeg=encode_jpeg(Image.new('RGB',(32,384),'gray'),90,2)
        cls.pack=pack([(cls.jpeg,32,384),(cls.jpeg,32,384)])
        cls.records=validate(cls.pack);cls.ftable=layout(len(cls.records))[1]

    def run_worker(self,corrupt=False,pointer=BUFFER,inner_status=0,inner_busy=0,index=0,mode=9,printing=0):
        blob=bytearray(build(BASE,self.pack,FLASH,BASE-0x2e000,BASE,SIZE))
        global_=bytearray(0x5000);struct.pack_into('<I',global_,0xe9c,mode)
        g=bytearray(b'\xa5'*32+b'\xcc'*2048+b'\x5a'*32);calls=[];owner=self
        expected=self.records[index] if index<len(self.records) else None
        class TestCPU(CPU):
            def access(self,address,size,value=None):
                if size==4 and value is None and (address-BIAS in (0x3ace0,0x3adb0,0x29790,0x2980c,0x39528) or address==BASE+INNER):
                    args=self.r[3:6].copy();calls.append((address,args));result=0
                    if address==BIAS+0x3ace0:
                        owner.assertEqual(args[:2],[((len(owner.jpeg)+15)&~15),32]);result=pointer
                    elif address==BIAS+0x39528:
                        off,n,*_=expected;rounded=(n+15)&~15
                        owner.assertEqual(args,[FLASH+off,BUFFER,rounded])
                        data=(owner.pack+b'\xff'*16)[off:off+rounded]
                        if corrupt:data=b'\0'*rounded
                        for i,b in enumerate(data):super().access(BUFFER+i,1,b)
                    elif address==BASE+INNER:
                        owner.assertEqual(args[:2],[index,printing])
                        slot=owner.ftable+index*16
                        owner.assertEqual(struct.unpack_from('<4I',blob,slot),(BUFFER,len(owner.jpeg),32,index+1))
                        owner.assertEqual(bytes(g[32:32+len(owner.jpeg)]),owner.jpeg)
                        struct.pack_into('<I',blob,INNER+STATE,inner_busy);result=inner_status
                    elif address==BIAS+0x3adb0:owner.assertEqual(args[0],BUFFER)
                    for r in (3,4,5,6,7,8,11,12,13,15,16,17):self.r[r]=0xaabb0000+r
                    self.r[11]=result;return 0x44004800
                return super().access(address,size,value)
        cpu=TestCPU([(BASE,blob,True),(BUFFER-32,g,True),(0x02085000,global_,False),(0x021ff000,bytearray(4096),True)])
        cpu.r=[0x12340000+i for i in range(32)];cpu.r[0]=0;cpu.r[1]=0x02200000;cpu.r[9]=0xfffffffc
        cpu.r[3:5]=[index,printing];before=cpu.r.copy();cpu.run(BASE,limit=300000)
        before[11]=cpu.r[11];self.assertEqual(cpu.r,before)
        self.assertEqual(g[:32]+g[-32:],b'\xa5'*32+b'\x5a'*32)
        return cpu,blob,calls

    def test_valid_reads_free_and_zero_selected_pointer(self):
        for index,printing,status in ((0,0,0),(1,1,0),(1,1,9)):
            cpu,blob,calls=self.run_worker(index=index,printing=printing,mode=6 if printing else 9,inner_status=status)
            self.assertEqual(cpu.r[11],status)
            self.assertEqual([c[0] for c in calls],[BIAS+0x3ace0,BIAS+0x29790,BIAS+0x39528,BIAS+0x2980c,BASE+INNER,BIAS+0x3adb0])
            self.assertEqual(struct.unpack_from('<I',blob,self.ftable+index*16)[0],0)
            self.assertEqual(struct.unpack_from('<3I',blob,CONTROL),(0,status,0))

    def test_corruption_is_freed_without_decoder_or_printer(self):
        cpu,blob,calls=self.run_worker(corrupt=True)
        self.assertEqual(cpu.r[11],8);self.assertNotIn(BASE+INNER,[c[0] for c in calls])
        self.assertEqual(calls[-1][0],BIAS+0x3adb0)
        self.assertEqual(struct.unpack_from('<I',blob,CONTROL)[0],0)

    def test_no_free_or_read_for_invalid_allocator(self):
        for pointer in (0,1,BASE,0x02099e00,0xffffffe0,0x021ff000):
            cpu,blob,calls=self.run_worker(pointer=pointer)
            self.assertEqual(cpu.r[11],8);self.assertEqual([c[0] for c in calls],[BIAS+0x3ace0])
            self.assertEqual(struct.unpack_from('<I',blob,CONTROL)[0],int(pointer!=0))

    def test_inner_fault_retains_busy_and_no_free(self):
        cpu,blob,calls=self.run_worker(inner_busy=1,inner_status=8)
        self.assertEqual(cpu.r[11],8);self.assertEqual(calls[-1][0],BASE+INNER)
        self.assertEqual(struct.unpack_from('<I',blob,CONTROL)[0],1)

    def test_wrong_mode_or_index_is_side_effect_free(self):
        for index,mode,printing in ((2,9,0),(0,6,0),(0,9,1),(0,3,1),(0,9,2)):
            cpu,_,calls=self.run_worker(index=index,mode=mode,printing=printing)
            self.assertEqual(cpu.r[11],8);self.assertEqual(calls,[])

    def test_geometry_and_flash_bounds(self):
        for offset in (FLASH+1,0x1ff000,0x3ff000):
            # Last offset fits this tiny pack, so use the real end boundary.
            if offset==0x3ff000:offset=0x400000
            with self.assertRaises(ValueError):build(BASE,self.pack,offset,BASE,BASE,SIZE)


class LargeCollectionTests(FlashWorkerTests):
    """Update 38: 17..64 photos use the extended directories (reader-slot tail)."""
    @classmethod
    def setUpClass(cls):
        cls.jpeg=encode_jpeg(Image.new('RGB',(32,384),'gray'),90,2)
        cls.pack=pack([(cls.jpeg,32,384)]*40)
        cls.records=validate(cls.pack);cls.ftable=layout(len(cls.records))[1]

    def test_layout(self):
        self.assertEqual(self.ftable,FTABLE_EXT);self.assertEqual(layout(16)[1],FTABLE);self.assertEqual(layout(17)[1],FTABLE_EXT)
        with self.assertRaises(ValueError):layout(65)

    def test_last_photo_reads(self):
        cpu,blob,calls=self.run_worker(index=39)
        self.assertEqual(cpu.r[11],0);self.assertIn(BASE+INNER,[c[0] for c in calls])

    def test_wrong_mode_or_index_is_side_effect_free(self):
        for index,mode,printing in ((40,9,0),(0,6,0),(0,9,1),(0,3,1),(0,9,2)):
            cpu,_,calls=self.run_worker(index=index,mode=mode,printing=printing)
            self.assertEqual(cpu.r[11],8);self.assertEqual(calls,[])


if __name__=='__main__':unittest.main()
