import struct
import unittest
from random_penguin_ram import build,worker,STATE,EVENT,TABLE,BIAS,GUARD_NAMES
from or1k_subset import CPU
from menu_controller import STATUS,DISPLAY,ACTIVATE

BASE=0x02136c80
MENU=0x02134e00
VIRTUAL=MENU-0x2b800
PIXELS=0x02160000
FEED=32


class RandomTests(unittest.TestCase):
    def setup_cpu(self,mode=9,printing=0,decoder_status=0,print_status=0,pointer=PIXELS,width=FEED,jpeg_pointer=None,power_state=5,table=TABLE):
        blob=bytearray(build(BASE,[(b'1234',FEED,1),(b'5678',FEED,14)],MENU,7808,VIRTUAL))
        if jpeg_pointer is not None:
            code=worker(BASE,len(blob),2,MENU,7808,input_disjoint=True,table=table);blob[:len(code)]=code
            struct.pack_into('<I',blob,TABLE,jpeg_pointer)
        below=bytearray(0x400)
        if table!=TABLE:   # update 38: directory below the worker (flash worker, 17..64 photos)
            below[table+0x400:table+0x400+32]=blob[TABLE:TABLE+32];blob[TABLE:TABLE+32]=b'\0'*32
        struct.pack_into('<I',blob,STATE+40,1)
        globals_=bytearray(0x5000);struct.pack_into('<I',globals_,0xe9c,mode)
        globals_[0x1970]=1;globals_[0x42c9]=power_state;globals_[0x4344]=3
        pixels=bytearray(range(256))*(FEED*576//256)
        calls=[];printed=[];owner=self
        class TestCPU(CPU):
            def access(self,address,size,value=None):
                helpers=(0x4ba2c,0x29790,0x4bed8,0x4cf20,0x3adb0,0x368fc)
                if value is None and size==4 and address-BIAS in helpers:
                    args=self.r[3:9].copy();calls.append((address-BIAS,args))
                    result=0
                    if address-BIAS==0x4ba2c:
                        owner.assertEqual(args[0],BASE+0x1000 if jpeg_pointer is None else jpeg_pointer)
                        owner.assertEqual(args[4:],[4,0]);owner.assertEqual(super().access(self.r[1],4),0)
                        super().access(args[1],4,pointer);super().access(args[2],2,width);super().access(args[3],2,384)
                        result=decoder_status
                    elif address-BIAS==0x4cf20:
                        owner.assertEqual(args,[PIXELS,FEED,384,3,0,power_state]);printed.append(bytes(pixels));result=print_status
                    elif address-BIAS==0x3adb0:owner.assertEqual(args[0],PIXELS)
                    elif address-BIAS==0x368fc:owner.assertEqual(args[0],1)
                    for r in (3,4,5,6,7,8,11,12,13,15,16,17):self.r[r]=0xdead0000+r
                    self.r[11]=result
                    return 0x44004800
                return super().access(address,size,value)
        cpu=TestCPU([(BASE,blob,True),(BASE-0x400,below,True),(PIXELS,pixels,True),(0x02085000,globals_,True),
                     (0x021ff000,bytearray(4096),True)])
        cpu.r=[0x12340000+i for i in range(32)];cpu.r[0]=0;cpu.r[1]=0x02200000;cpu.r[9]=0xfffffffc
        cpu.r[3:5]=[0,printing]
        return cpu,blob,pixels,calls,printed,globals_

    def test_decode_correct_free_dry_and_print(self):
        for printing,print_status in ((0,0),(1,0),(1,8),(1,9),(1,11)):
            with self.subTest(printing=printing,status=print_status):
                cpu,blob,pixels,calls,printed,_=self.setup_cpu(mode=6 if printing else 9,printing=printing,print_status=print_status)
                before=cpu.r.copy();original=bytes(pixels);cpu.run(BASE,limit=200000)
                before[11]=print_status;self.assertEqual(cpu.r,before)
                expected=bytes((v*190+127)//255 for v in original[:FEED*384])+original[FEED*384:]
                self.assertEqual(bytes(pixels),expected)
                expected_calls=[0x4ba2c,0x29790]+([0x4bed8,0x4cf20,0x4bed8] if printing else [])+[0x3adb0,0x368fc]
                self.assertEqual([c[0] for c in calls],expected_calls)
                if printing:
                    self.assertEqual(printed,[expected]);self.assertEqual(calls[2][1][0],0);self.assertEqual(calls[4][1][0],1)
                self.assertEqual(struct.unpack_from('<4I',blob,STATE),(0,print_status,2,1))

    def test_directory_below_worker(self):
        cpu,blob,pixels,calls,printed,_=self.setup_cpu(jpeg_pointer=0x02170000,table=-0x400)
        cpu.run(BASE,limit=200000)
        self.assertEqual(cpu.r[11],0);self.assertEqual([c[0] for c in calls][:1],[0x4ba2c])

    def test_errors_never_print(self):
        for pointer,width,status,freed,busy in ((0,FEED,4,False,0),(0,FEED,0,False,0),
             (PIXELS,FEED,4,True,0),(PIXELS,64,0,False,1),(BASE,FEED,0,False,1),
             (MENU,FEED,0,False,1),(0xffffffe0,FEED,0,False,1),(0x02099e00,FEED,0,False,1)):
            with self.subTest(pointer=pointer,width=width,status=status):
                cpu,blob,_,calls,_,_=self.setup_cpu(mode=6,printing=1,decoder_status=status,pointer=pointer,width=width)
                cpu.run(BASE,limit=5000)
                self.assertEqual([c[0] for c in calls],[0x4ba2c]+([0x3adb0] if freed else [])+[0x368fc])
                self.assertEqual(struct.unpack_from('<I',blob,STATE)[0],busy)
                self.assertEqual(cpu.r[11],status or 8)

    def test_guarded_refusals(self):
        for reason in ('mode','printer_busy','worker_busy','not_armed','index','print_flag','density','settings','lcd'):
            with self.subTest(reason=reason):
                cpu,blob,_,calls,_,g=self.setup_cpu(mode=6,printing=1)
                if reason=='mode':struct.pack_into('<I',g,0xe9c,9)
                elif reason=='printer_busy':struct.pack_into('<I',g,0x3414,1)
                elif reason=='worker_busy':struct.pack_into('<I',blob,STATE,1)
                elif reason=='not_armed':struct.pack_into('<I',blob,STATE+40,0)
                elif reason=='index':cpu.r[3]=2
                elif reason=='print_flag':cpu.r[4]=2
                elif reason=='density':g[0x4344]=4
                elif reason=='settings':g[0x42c9]=6
                elif reason=='lcd':g[0x1970]=2
                cpu.run(BASE,limit=1000);self.assertEqual(calls,[]);self.assertEqual(cpu.r[11],8)

    def test_diagnostic_refusals_keep_guards_and_abi(self):
        for reason in GUARD_NAMES:
            cpu,blob,_,calls,_,g=self.setup_cpu(mode=6,printing=1)
            code=worker(BASE,len(blob),2,MENU,7808,diagnostics=True)
            blob[:EVENT]=code.ljust(EVENT,b'\0')
            if reason=='worker_busy':struct.pack_into('<I',blob,STATE,1)
            elif reason=='index':cpu.r[3]=2
            elif reason=='print_flag':cpu.r[4]=2
            elif reason=='printer_busy':struct.pack_into('<I',g,0x3414,1)
            elif reason=='print_mode':struct.pack_into('<I',g,0xe9c,9)
            elif reason=='not_armed':struct.pack_into('<I',blob,STATE+40,0)
            elif reason=='dry_mode':cpu.r[4]=0
            elif reason=='settings':g[0x42c9]=6
            elif reason=='density':g[0x4344]=4
            elif reason=='lcd':g[0x1970]=2
            before=cpu.r.copy();before[11]=8
            cpu.run(BASE,limit=1000)
            self.assertEqual(cpu.r,before)
            self.assertEqual(calls,[])
            record=struct.unpack_from('<5I',blob,STATE+44)
            self.assertEqual(record[:3],(GUARD_NAMES.index(reason)+1,before[3],before[4]))

    def test_diagnostic_success_preserves_processing(self):
        cpu,blob,_,calls,printed,_=self.setup_cpu(mode=6,printing=1)
        code=worker(BASE,len(blob),2,MENU,7808,diagnostics=True)
        blob[:EVENT]=code.ljust(EVENT,b'\0')
        struct.pack_into('<I',blob,STATE+44,123)
        cpu.run(BASE,limit=200000)
        self.assertEqual(cpu.r[11],0)
        self.assertEqual(struct.unpack_from('<I',blob,STATE+44)[0],0)
        self.assertEqual(len(printed),1)

    def test_flash_input_alias_latches_without_free(self):
        for jpeg_pointer in (PIXELS,PIXELS+1024,PIXELS-32):
            cpu,blob,_,calls,_,_=self.setup_cpu(jpeg_pointer=jpeg_pointer)
            cpu.run(BASE,limit=200000)
            if jpeg_pointer==PIXELS-32:  # 16-byte rounded input ends before output
                self.assertEqual(cpu.r[11],0)
                self.assertEqual(calls[-2][0],0x3adb0)
            else:
                self.assertEqual(cpu.r[11],8)
                self.assertEqual([c[0] for c in calls],[0x4ba2c,0x368fc])
                self.assertEqual(struct.unpack_from('<I',blob,STATE)[0],1)

    def test_power_state_six_fix_is_exactly_one_immediate(self):
        for diagnostics in (False,True):
            old=worker(BASE,0x1400,2,MENU,7808,diagnostics=diagnostics)
            new=worker(BASE,0x1400,2,MENU,7808,diagnostics=diagnostics,max_power_state=6)
            differences=[i for i in range(0,len(old),4) if old[i:i+4]!=new[i:i+4]]
            self.assertEqual(len(differences),1)
            offset=differences[0]
            self.assertEqual(struct.unpack_from('<I',old,offset)[0],0xbc4c0005)
            self.assertEqual(struct.unpack_from('<I',new,offset)[0],0xbc4c0006)

    def test_power_state_six_passed_unchanged_to_guarded_stock_printer(self):
        for state in (5,6,7,255):
            cpu,blob,_,calls,printed,_=self.setup_cpu(mode=6,printing=1,power_state=state)
            code=worker(BASE,len(blob),2,MENU,7808,diagnostics=True,max_power_state=6)
            blob[:EVENT]=code.ljust(EVENT,b'\0')
            cpu.run(BASE,limit=200000)
            self.assertEqual(cpu.r[11],0 if state<=6 else 8)
            self.assertEqual(len(printed),1 if state<=6 else 0)
            if state>6:
                self.assertEqual(calls,[])
                self.assertEqual(struct.unpack_from('<I',blob,STATE+44)[0],8)

    def test_rotation_corrects_every_luma_pixel_and_preserves_chroma_abi(self):
        for diagnostics in (False,True):
            for printing in (0,1):
                cpu,blob,pixels,calls,printed,_=self.setup_cpu(
                    mode=6 if printing else 9,printing=printing,power_state=6)
                # Nonperiodic pattern, including distinctive corners/center.
                for i in range(len(pixels)):pixels[i]=(i*17+(i//FEED)*23+(i//384)*7)%256
                original=bytes(pixels);before=cpu.r.copy()
                code=worker(BASE,len(blob),2,MENU,7808,diagnostics=diagnostics,
                            max_power_state=6,rotate180=True)
                blob[:EVENT]=code.ljust(EVENT,b'\0')
                cpu.run(BASE,limit=200000);before[11]=0
                expected=bytes((v*190+127)//255 for v in original[:FEED*384][::-1])+original[FEED*384:]
                self.assertEqual(bytes(pixels),expected)
                self.assertEqual(cpu.r,before)
                self.assertEqual(printed,[expected] if printing else [])
                self.assertEqual([c[0] for c in calls],
                    [0x4ba2c,0x29790]+([0x4bed8,0x4cf20,0x4bed8] if printing else [])+[0x3adb0,0x368fc])
                self.assertEqual(struct.unpack_from('<4I',blob,STATE),(0,0,2,1))

    def test_activation_chooses_without_immediate_repeat_and_preserves_other_actions(self):
        for selection,subtype,payload in ((2,1,0),(2,1,2),(2,2,0),(0,1,0),(1,1,0)):
            with self.subTest(selection=selection,subtype=subtype,payload=payload):
                blob=bytearray(build(BASE,[(b'1234',FEED,1),(b'5678',FEED,14)],MENU,7808,VIRTUAL))
                g=bytearray(0x5000);g[0x4344]=3;g[0x4334]=selection
                struct.pack_into('<I',g,0x4400,payload)
                menu=bytearray(7808);calls=[];chosen=[]
                class EventCPU(CPU):
                    def access(self,address,size,value=None):
                        if value is None and size==4 and address in (BASE,VIRTUAL+DISPLAY,VIRTUAL+ACTIVATE):
                            calls.append(address)
                            if address==BASE:
                                chosen.append(self.r[3]);struct.pack_into('<I',blob,STATE+16,self.r[3])
                            self.r[11]=0
                            return 0x44004800
                        return super().access(address,size,value)
                cpu=EventCPU([(BASE,blob,True),(0x02085000,g,False),(MENU,menu,True),(0x021ff000,bytearray(4096),True)])
                cpu.r[1]=0x02200000;cpu.r[4:6]=[subtype,0x02089400]
                for i in range(8):cpu.run(BASE+EVENT,limit=10000)
                if selection==2 and subtype==1 and payload==0:
                    self.assertEqual(calls,[BASE,VIRTUAL+DISPLAY]*8)
                    self.assertTrue(all(0<=v<2 for v in chosen))
                    self.assertTrue(all(a!=b for a,b in zip(chosen,chosen[1:])))
                else:self.assertEqual(calls,[VIRTUAL+ACTIVATE]*8)

    def test_one_image_selection_stays_in_bounds(self):
        blob=bytearray(build(BASE,[(b'1234',FEED,5)],MENU,7808,VIRTUAL))
        g=bytearray(0x5000);g[0x4334]=2
        menu=bytearray(7808);chosen=[]
        class EventCPU(CPU):
            def access(self,address,size,value=None):
                if value is None and size==4 and address in (BASE,VIRTUAL+DISPLAY):
                    if address==BASE:
                        chosen.append(self.r[3]);struct.pack_into('<I',blob,STATE+16,self.r[3])
                    self.r[11]=0;return 0x44004800
                return super().access(address,size,value)
        cpu=EventCPU([(BASE,blob,True),(0x02085000,g,False),(MENU,menu,True),
                     (0x021ff000,bytearray(4096),True)])
        cpu.r[1]=0x02200000;cpu.r[4:6]=[1,0x02089400]
        for _ in range(16):cpu.run(BASE+EVENT,limit=10000)
        self.assertEqual(chosen,[0]*16)


if __name__=='__main__':unittest.main()
