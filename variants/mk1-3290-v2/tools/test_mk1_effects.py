"""Offline native execution tests. SDK/peripherals stubbed: this is not boot/hardware validation."""
import random, struct, unittest
import build_mk1_effects as b
from mk1_native import CPU, HEAP, RAM_END, BIAS
from penguin_port import color_gray, gray_filters, tone_gray, date_stamp
from penguin_port.effects_v5 import bayer8_thresholds, halftone

CALLEE={2,14,18,20,22,24,26,28,30}
Y=0x02100000; UV=0x02200000; SCRATCH=0x02400000; O=0x02170a00

def floyd(src,w,h):
    out=bytearray(src); errors=[[0]*w for _ in range(3)]
    for y in range(h):
        step=1 if y%2==0 else -1
        for x in (range(w) if step==1 else range(w-1,-1,-1)):
            v=min(255*256,max(0,out[y*w+x]*256+errors[y%3][x])); q=255 if v>=128*256 else 0
            out[y*w+x]=q; e=v-q*256
            for dx,dy,n in ((1,0,7),(-1,1,3),(0,1,5),(1,1,1)):
                xx=x+dx*step; yy=y+dy
                if 0<=xx<w and yy<h:
                    delta=abs(e*n)//16*(-1 if e<0 else 1)
                    errors[yy%3][xx]+=delta
        errors[y%3]=[0]*w
    return bytes(out)

class Harness(CPU):
    def __init__(self):
        self.image,self.manifest=b.build(); self.sym=self.manifest['symbols']
        self.ram=bytearray(0x800000); self.ram[:0xc1a00]=self.image[0x2600:0xc4000]
        super().__init__([(0x02000000,self.ram,True)])
        self.r[1]=0x027f0000; self.allocs={}; self.next=SCRATCH; self.oom=False; self.calls=[]
        self.put(b.G+88,b'\x01'); self.put(b.G+95,b'\x01'); self.put(0x020c1ab8,struct.pack('<I',date_stamp.SETTING_ON))
        self.put(0x020c7c74,struct.pack('<HBB',2026,10,3)); self.put(0x020bbbd4,struct.pack('<10I',*range(101,111)))
    def put(self,a,data): self.ram[a-0x02000000:a-0x02000000+len(data)]=data
    def get(self,a,n): return bytes(self.ram[a-0x02000000:a-0x02000000+n])
    def state(self,off=0,value=None):
        a=self.sym['state']['ram']+off
        if value is not None: self.put(a,struct.pack('<I',value&0xffffffff))
        return int.from_bytes(self.get(a,4),'little')
    def access(self,a,size,value=None):
        if value is None and size==4 and a in (b.MALLOC,b.FREE,b.FLUSH,0x02056870,0x020444e0,0x020543b4,
               0x02043d20,0x02008b1c,0x020089a8,0x0202a868,0x0203aa34,0x020391c0,0x0205831c,0x0200819c):
            original=self.r[:]; self.calls.append((a,original[3:11])); result=0
            if a==b.MALLOC:
                if not self.oom:
                    result=(self.next+31)&~31; n=original[3]; self.next=result+n+64
                    assert self.next<RAM_END-0x10000; self.allocs[result]=n
            elif a==b.FREE:
                assert original[3] in self.allocs, f'free of unowned {original[3]:#x}'
                del self.allocs[original[3]]
            elif a==b.FLUSH:
                assert HEAP<=original[3]<RAM_END and original[3]+original[4]<=RAM_END, ('cache bounds',original[3:5])
            for r in range(3,32):
                if r not in CALLEE and r not in (9,): self.r[r]=0xabab0000+r
            self.r[11]=result
            if a in (0x0203aa34,0x020391c0,0x0205831c,0x0200819c): self.r[9]=0xfffffffc
            return 0x44004800
        return super().access(a,size,value)
    def invoke(self,name,params):
        self.steps=0; self.writes=[]
        for r,v in params.items(): self.r[r]=v
        original=self.r[:]; original[9]=0xfffffffc
        self.run(self.sym[name]['ram'],limit=30000000)
        return original
    def pixels(self,w,h,seed=5):
        rng=random.Random(seed); y=rng.randbytes(w*h); uv=rng.randbytes(w*h//2)
        self.put(Y,y); self.put(UV,uv); return y+uv

class NativeTests(unittest.TestCase):
    def test_image_and_usb_gate(self):
        image,m=b.build(); base=(b.ROOT/'analysis/build_01/image.bin').read_bytes()
        self.assertEqual(image[:0x2600],base[:0x2600]); self.assertEqual(image[0xc4000:],base[0xc4000:])
        self.assertEqual(struct.unpack_from('<I',image,0x4b318)[0],0x9c600000)
        self.assertLessEqual(m['payload_used'],b.SIZE)
        # Every byte outside declared exact patches must be unchanged.
        patched=bytearray(base)
        for p in m['patches']: patched[p['offset']:p['offset']+p['length']]=image[p['offset']:p['offset']+p['length']]
        self.assertEqual(patched,image)
    def test_color_gray_separate_planes_and_registers(self):
        h=Harness(); src=h.pixels(64,32); before=h.invoke('orange',{3:Y,4:64,5:32,6:UV})
        self.assertEqual(h.get(Y,2048),color_gray.reference(src,64,32)); self.assertEqual(h.get(UV,1024),src[2048:])
        self.assertEqual(h.r,before)
    def test_all_modes_both_print_states(self):
        for print_on in (0,1):
            for mode in (0,1,2,3):
                with self.subTest(print_on=print_on,mode=mode):
                    h=Harness(); src=h.pixels(64,32); h.put(b.G+95,bytes([print_on]))
                    before=h.invoke('transform',{3:Y,4:64,5:32,6:UV,7:1,8:mode})
                    y=color_gray.reference(src,64,32)
                    if mode:
                        y=gray_filters.apply_reference(y,0 if mode==1 else 2,4)
                        if mode==1:
                            t=bayer8_thresholds(); y=bytes(255 if v>=t[(i//64%8)*8+i%8] else 0 for i,v in enumerate(y))
                        elif mode==2: y=halftone(y,64,32)
                        else: y=floyd(y,64,32)
                    elif not print_on: y=src[:2048]
                    self.assertEqual(h.get(Y,2048),bytes(y)); self.assertEqual(h.get(UV,1024),bytes([128])*1024 if mode or print_on else src[2048:])
                    self.assertEqual(h.r,before); self.assertFalse(h.allocs); self.assertEqual(h.state(8),0)
    def test_invalid_and_oom_do_not_touch_image(self):
        for params,oom in (({4:7},False),({5:31},False),({6:Y},False),({3:0x02076000},False),({},True)):
            h=Harness(); h.pixels(64,32); original=h.get(Y,2048); original_uv=h.get(UV,1024); h.oom=oom
            h.invoke('transform',{3:Y,4:64,5:32,6:UV,7:1,8:2,**params})
            self.assertEqual(h.get(Y,2048),original); self.assertEqual(h.get(UV,1024),original_uv); self.assertFalse(h.allocs)
    def test_controls_frames_and_effects(self):
        for printing in (0,1):
            h=Harness(); h.put(b.G+95,bytes([printing])); h.put(0x020c6000,struct.pack('<I',2))
            for key,expected in [('left',-1),('left',-2),('right',-1),('right',0),('right',1),('right',2),('right',3),('right',0)]:
                h.invoke(key,{3:0x020c7000,4:1,5:0x020c6000})
                self.assertEqual(h.state(),expected&0xffffffff); self.assertEqual(h.get(b.G+91,1),b'\0')
                self.assertEqual(h.get(b.G+89,1),bytes([3 if expected<0 else 0]))
            for k in range(7): h.invoke('left',{3:0x020c7000,4:1,5:0x020c6000})
            self.assertEqual(h.state(),(-7)&0xffffffff); self.assertEqual(h.get(b.G+94,1),b'\x06')
            frames=[c[1][0] for c in h.calls if c[0]==0x02043d20]; self.assertTrue(all(101<=x<=107 for x in frames))
    def test_capture_snapshot_owned_and_banks(self):
        for bank in (1,2):
            h=Harness(); src=h.pixels(64,32); h.state(value=2)
            h.put(b.CAPTURE+12,struct.pack('<HH',64,32)); h.put(b.CAPTURE+40,struct.pack('<I',bank))
            h.put(b.CAPTURE+(24 if bank==2 else 32),struct.pack('<II',Y,UV))
            before=h.invoke('capture',{})
            raw=h.state(12); self.assertEqual(h.get(raw,len(src)),src); self.assertIn(raw,h.allocs)
            self.assertEqual(h.get(Y,2048),halftone(gray_filters.apply_reference(color_gray.reference(src,64,32),2,4),64,32))
            self.assertEqual(h.r,before); h.invoke('cleanup',{}); self.assertFalse(h.allocs); self.assertEqual(h.state(12),0)
    def test_resample_uv_pairs(self):
        h=Harness(); src=h.pixels(64,32); h.put(SCRATCH,src)
        h.invoke('resample',{3:Y+0x2000,4:32,5:16,6:UV+0x2000,7:SCRATCH,8:64,10:32})
        expected=bytes(src[y*2*64+x*2] for y in range(16) for x in range(32))
        self.assertEqual(h.get(Y+0x2000,512),expected)
        expected_uv=b''.join(src[2048+y*2*64+x*2:2048+y*2*64+x*2+2] for y in range(8) for x in range(0,32,2))
        self.assertEqual(h.get(UV+0x2000,256),expected_uv)
    def test_date_and_tone_oracles(self):
        for w,hh in ((24,384),(384,30)):
            h=Harness(); src=h.pixels(w,hh)[:w*hh]
            h.invoke('date',{3:Y,4:w,5:hh}); self.assertEqual(h.get(Y,w*hh),date_stamp.reference(src,w,hh,2026,10,3))
        h=Harness(); src=h.pixels(20,384)[:20*384]
        h.invoke('tone',{3:Y,4:20,5:384}); self.assertEqual(h.get(Y,len(src)),tone_gray.reference(src,20,384,tone_gray.tables()))
    def test_printer_mode_zero_and_rotation(self):
        h=Harness(); w,hh=384,30; src=h.pixels(w,hh); h.put(Y,src); h.state(value=0)
        gray=color_gray.reference(src,w,hh); rotated=bytes(gray[y*w+(383-x)] for x in range(384) for y in range(hh))
        toned=tone_gray.reference(rotated,hh,384,tone_gray.tables())
        expected=bytes(toned[(383-x)*hh+y] for y in range(hh) for x in range(w))
        expected=date_stamp.reference(expected,w,hh,2026,10,3)
        h.invoke('printer',{3:Y,4:w,5:hh,6:2,7:1,8:3})
        self.assertEqual(h.get(Y,w*hh),expected); self.assertFalse(h.allocs)
        call=[c for c in h.calls if c[0]==0x0205831c][-1]; self.assertEqual(call[1][4],0); self.assertEqual(call[1][3],2)
    def test_labels_osd_only_and_removal(self):
        for mode in (1,2,3):
            h=Harness(); src=h.pixels(64,32); h.state(value=mode)
            h.put(b.OSD+8,struct.pack('<I',0x014000f0)); h.put(b.OSD+16,struct.pack('<II',O,76800))
            h.put(b.OSD+24,struct.pack('<II',O+76800,76800))
            original=bytearray([249])*76800
            for x in range(320):
                for y in range(32): original[(319-x)*240+y]=(x*7+y*11)%249
            h.put(O,original)
            h.invoke('labels',{3:0,4:O}); osd=h.get(O,76800)
            self.assertIn(251,osd); self.assertEqual(h.get(Y,2048),src[:2048]); self.assertEqual(h.get(UV,1024),src[2048:])
            self.assertTrue(all(osd[(319-x)*240+y]==original[(319-x)*240+y]
                for x in range(320) for y in range(240) if x>=224 or y<32 or y>=64))
            self.assertEqual({osd[(319-x)*240+y] for x in range(224) for y in range(32,64)}, {249,251})
            h.state(value=0); h.invoke('labels',{3:0,4:O}); self.assertEqual(h.get(O,76800),original)
if __name__=='__main__': unittest.main()
