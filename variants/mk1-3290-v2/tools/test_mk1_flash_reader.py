import sys,struct,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from mk1_usb_flash import Code,SIZE,FLUSH,READ,INVALIDATE
from mk1_flash_reader import reader
from penguin_port.or1k_subset import CPU

class ReaderTests(unittest.TestCase):
    origin=0x02100000;result=origin+512;base=0x02300000
    def run_reader(self,off,length):
        code=reader(self.origin,self.result,self.base,length)
        scratch=bytearray(1024);scratch[:len(code)]=code;struct.pack_into('<II',scratch,512,off,0x83a17f12)
        data=bytearray(b'\xee'*(length+256));stack=bytearray(1024)
        # A simulated READ marks the first word only. It is enough to check call args,
        # guards, poison and denial paths without pretending to emulate DMA/cache.
        read_code=Code(READ);read_code.emit(0xd4041800);read_code.emit(0xd4042804);read_code.emit(0x9d600000);read_code.emit(0x44004800)
        noop=bytearray(struct.pack('<I',0x44004800))
        cpu=CPU([(self.origin,scratch,True),(self.base,data,True),(0x02700000,stack,True),
                 (FLUSH,noop,False),(INVALIDATE,noop,False),(READ,bytearray(read_code.finish()),False)])
        cpu.r[1]=0x027003c0;cpu.r[3]=self.result;cpu.run(self.origin)
        self.assertEqual(cpu.r[1],0x027003c0)
        return struct.unpack_from('<II',scratch,512),data
    def test_valid_and_repeated_reads(self):
        for length in [16,4096,65536]:
            for off in [0,SIZE-length]:
                status,data=self.run_reader(off,length)
                self.assertEqual(status,(0,0x1970));self.assertEqual(data[:64],b'\xa5'*64)
                self.assertEqual(struct.unpack_from('<II',data,64),(off,length))
                self.assertEqual(data[72:64+length],struct.pack('<I',0x83a17f12)*((length-8)//4))
                self.assertEqual(data[64+length:128+length],b'\x5a'*64)
                self.assertEqual(data[128+length:],b'\xee'*128)
    def test_invalid_offsets_do_not_touch_buffer(self):
        for off in [1,4095,SIZE-4096+16,SIZE,0xffffffff]:
            status,data=self.run_reader(off,4096)
            self.assertEqual(status,(0xffffffff,0x1970));self.assertEqual(data,b'\xee'*len(data))
if __name__=='__main__':unittest.main()
