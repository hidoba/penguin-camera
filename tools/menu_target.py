"""Native OpenRISC text-menu renderer, independently testable without hardware.

Caller owns a 320x240 linear Y buffer and must handle chroma/cache/display.
This does not itself bind stock menu events or start the printer.
"""
import struct
from live_preview_patch import Code
from preview_labels import GLYPHS

DRAW=0x2b800
PORTRAIT=0x2bd00
FONT=0x2c100
TEXT=0x2c500
SIZE=0x2e000
ITEMS=('Camera','Settings','Random penguin')
FOOTER='penguin camera v1 by Vlad Grankovsky & Tinnix He'
LOWER={
 'a':(32,84,84,84,120),'b':(127,72,68,68,56),'c':(56,68,68,68,32),
 'd':(56,68,68,72,127),'e':(56,84,84,84,24),'f':(8,126,9,1,2),
 'g':(12,82,82,82,62),'h':(127,8,4,4,120),'i':(0,68,125,64,0),
 'j':(32,64,68,61,0),'k':(127,16,40,68,0),'l':(0,65,127,64,0),
 'm':(124,4,24,4,120),'n':(124,8,4,4,120),'o':(56,68,68,68,56),
 'p':(124,20,20,20,8),'q':(8,20,20,24,124),'r':(124,8,4,4,8),
 's':(72,84,84,84,32),'t':(4,63,68,64,32),'u':(60,64,64,32,124),
 'v':(28,32,64,32,28),'w':(60,64,48,64,60),'x':(68,40,16,40,68),
 'y':(12,80,80,80,60),'z':(68,100,84,76,68),
 '&':(54,73,85,34,80),'>':(0,65,34,20,8)}
GLYPH={**GLYPHS,**LOWER}
LINES=[(40,52+i*44,2,text) for i,text in enumerate(ITEMS)]+[
 ((320-len(text)*6)//2,y,1,text) for text,y in
 (("penguin camera v1 by",210),("Vlad Grankovsky & Tinnix He",222))]+[(16,52,2,'>')]

def data():
    font=bytearray(640)
    for c,cols in GLYPH.items():font[ord(c)*5:ord(c)*5+5]=bytes(cols)
    records=[]
    for x,y,scale,text in LINES:
        assert len(text)<32 and all(c in GLYPH for c in text)
        records.append(struct.pack('<4I',x,y,scale,len(text))+text.encode().ljust(32,b'\0'))
    return bytes(font),b''.join(records)

def protected_range(base,protected=None,allocation_size=SIZE):
    start,end=(base,base+SIZE) if protected is None else protected
    if not base<=start<end<=base+allocation_size or start%4 or end%4 or end>0x02200000:
        raise ValueError('invalid menu protected range')
    return start,end

def renderer(base,portrait=False,protected=None,allocation_size=SIZE):
    """r3 = Y pixels, r4 = selected 0..2, r11=status; other registers kept."""
    protect_start,protect_end=protected_range(base,protected,allocation_size)
    a=Code();imm=a.immediate
    saved=tuple(r for r in range(2,31) if r!=11)
    imm(0x27,1,1,-112)
    for i,r in enumerate(saved):a.store(r,1,i*4)
    imm(0x2f,2,4,2);a.branch(4,'reject')
    a.const(12,0x02090000);a.compare(3,12,4);a.branch(4,'reject')
    plane_bytes=81920 if portrait else 76800
    a.const(12,0x02200000-plane_bytes);a.compare(3,12,2);a.branch(4,'reject')
    imm(0x29,12,3,3);imm(0x2f,1,12,0);a.branch(4,'reject')
    a.const(12,plane_bytes);a.alu(13,3,12)
    a.compare(13,1,2);a.branch(4,'reject')
    a.const(12,protect_end);a.compare(3,12,3);a.branch(4,'disjoint')
    a.const(12,protect_start);a.compare(13,12,2);a.branch(4,'reject')
    a.label('disjoint');imm(0x2a,18,3,0);imm(0x2a,20,4,0)
    imm(0x2a,12,3,0);imm(0x27,13,0,plane_bytes//4)
    a.label('clear');a.store(0,12);imm(0x27,12,12,4);imm(0x27,13,13,-1)
    imm(0x2f,1,13,0);a.branch(4,'clear')
    a.const(22,base+TEXT);a.const(24,base+FONT);imm(0x27,26,0,0)
    a.label('line');imm(0x21,2,22,0);imm(0x21,12,22,4)
    imm(0x2f,1,26,5);a.branch(4,'line_y')
    imm(0x27,13,0,44);a.alu(13,13,20,0x306);a.alu(12,12,13)
    a.label('line_y')
    if portrait:
        # Logical (x,y) -> LCD (y,319-x), 256-byte stride: 90 degrees CCW.
        imm(0x27,13,0,319);a.alu(2,13,2,2);imm(0x2e,2,2,8)
    else:
        imm(0x27,13,0,320);a.alu(12,12,13,0x306)
    a.alu(2,2,12);a.alu(2,2,18)
    imm(0x21,14,22,8);imm(0x21,16,22,12);imm(0x27,28,22,16)
    a.label('char');imm(0x23,12,28,0);imm(0x27,13,0,5)
    a.alu(12,12,13,0x306);a.alu(12,12,24);imm(0x27,17,0,0)
    a.label('column');imm(0x23,15,12,0);a.alu(3,17,14,0x306)
    if portrait:imm(0x2e,3,3,8);a.alu(3,0,3,2)
    a.alu(3,3,2)
    imm(0x27,19,0,7);imm(0x27,21,0,255)
    a.label('row');imm(0x29,13,15,1);imm(0x2f,0,13,0);a.branch(4,'blank')
    a.store(21,3,op=0x36);imm(0x2f,1,14,2);a.branch(4,'blank')
    for off in ((-256,1,-255) if portrait else (1,320,321)):a.store(21,3,off,op=0x36)
    a.label('blank');imm(0x27,13,0,1 if portrait else 320);a.alu(13,13,14,0x306);a.alu(3,3,13)
    imm(0x2e,15,15,0x41);imm(0x27,19,19,-1);imm(0x2f,1,19,0);a.branch(4,'row')
    imm(0x27,12,12,1);imm(0x27,17,17,1);imm(0x2f,4,17,5);a.branch(4,'column')
    imm(0x27,13,0,-1536 if portrait else 6);a.alu(13,13,14,0x306);a.alu(2,2,13)
    imm(0x27,28,28,1);imm(0x27,16,16,-1);imm(0x2f,1,16,0);a.branch(4,'char')
    imm(0x27,22,22,48);imm(0x27,26,26,1);imm(0x2f,4,26,len(LINES));a.branch(4,'line')
    imm(0x27,11,0,0);a.branch(0,'restore')
    a.label('reject');imm(0x27,11,0,8)
    a.label('restore')
    for i,r in enumerate(saved):imm(0x21,r,1,i*4)
    imm(0x27,1,1,112);a.emit(0x44004800)
    code=a.finish()
    if len(code)>(FONT-PORTRAIT if portrait else PORTRAIT-DRAW):raise ValueError('menu renderer overflow')
    return code

def reference(selected):
    if selected not in range(3):raise ValueError('invalid menu selection')
    pixels=bytearray(76800)
    for n,(x,y,scale,text) in enumerate(LINES):
        if n==5:y+=44*selected
        for i,c in enumerate(text):
            for col,bits in enumerate(GLYPH[c]):
                for row in range(7):
                    if bits&(1<<row):
                        for dy in range(scale):
                            for dx in range(scale):pixels[(y+row*scale+dy)*320+x+(i*6+col)*scale+dx]=255
    return bytes(pixels)
