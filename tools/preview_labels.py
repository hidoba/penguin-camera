"""Small code-native bitmap label overlay; never called by the print path."""
from live_preview_patch import Code

LABEL_CODE=0xd800
FONT=0xe900
LABELS=0xec00
TEXT=('GRAY 190/255','BAYER 8X8','BAYER 4X4','THRESHOLD','FLOYD-STEINBERG',
      'ATKINSON','STUCKI','CRACKED DIFFUSION','MAGIC 4X4 45','ERROR DIFFUSION 1D')   # mode 7 (Halftone 4x4, hidden) keeps libdither's name: this text is in the v1.0 image
# Five columns, low bit at top, seven rows. Deliberately limited uppercase UI.
GLYPHS={
    ' ': (0,0,0,0,0), '-':(8,8,8,8,8), '/':(32,16,8,4,2),
    '0':(62,81,73,69,62), '1':(0,66,127,64,0), '2':(66,97,81,73,70),
    '3':(33,65,69,75,49), '4':(24,20,18,127,16), '5':(39,69,69,69,57),
    '6':(60,74,73,73,48), '7':(1,113,9,5,3), '8':(54,73,73,73,54), '9':(6,73,73,41,30),
    'A':(126,17,17,17,126), 'B':(127,73,73,73,54), 'C':(62,65,65,65,34),
    'D':(127,65,65,34,28), 'E':(127,73,73,73,65), 'F':(127,9,9,9,1),
    'G':(62,65,73,73,122), 'H':(127,8,8,8,127), 'I':(0,65,127,65,0),
    'J':(32,64,65,63,1), 'K':(127,8,20,34,65), 'L':(127,64,64,64,64),
    'M':(127,2,12,2,127), 'N':(127,4,8,16,127), 'O':(62,65,65,65,62),
    'P':(127,9,9,9,6), 'Q':(62,65,81,33,94), 'R':(127,9,25,41,70),
    'S':(70,73,73,73,49), 'T':(1,1,127,1,1), 'U':(63,64,64,64,63),
    'V':(31,32,64,32,31), 'W':(63,64,56,64,63), 'X':(99,20,8,20,99),
    'Y':(7,8,112,8,7), 'Z':(97,81,73,69,67),
}


def font_data():
    data=bytearray(128*5)
    for char,columns in GLYPHS.items(): data[ord(char)*5:ord(char)*5+5]=bytes(columns)
    return bytes(data)


def label_data():
    assert all(len(text)<=24 and all(c in GLYPHS for c in text) for text in TEXT)
    return b''.join(text.encode().ljust(32,b'\0') for text in TEXT)


def overlay(base,top=0,*,show_gray=True,max_mode=None):
    if not 0<=top<=218:raise ValueError('Label strip outside preview')
    a=Code(); imm=a.immediate
    saved=(2,3,4,5,6,7,8,12,13,15)
    imm(0x27,1,1,-40)
    for i,r in enumerate(saved): a.store(r,1,i*4)
    # Normal preview must remain untouched, including the label's black strip.
    if not show_gray:
        imm(0x2f,0,4,0); a.branch(4,'done')
    imm(0x2f,2,4,1); a.branch(4,'done')
    imm(0x2f,2,5,len(TEXT)-2 if max_mode is None else max_mode); a.branch(4,'done')
    a.const(12,0x02090000); a.compare(3,12,4); a.branch(4,'done')
    extent=(top+22)*320
    a.const(12,0x02200000-extent); a.compare(3,12,2); a.branch(4,'done')
    imm(0x29,12,3,3); imm(0x2f,1,12,0); a.branch(4,'done')
    if top:a.const(12,extent);a.alu(12,3,12)
    else:imm(0x27,12,3,7040)
    a.compare(12,1,2); a.branch(4,'done')
    imm(0x2f,1,4,0); a.branch(4,'effect')
    imm(0x27,5,0,-1)
    a.label('effect'); imm(0x27,5,5,1); imm(0x2e,5,5,5)
    a.const(6,base+LABELS); a.alu(6,6,5); a.const(7,base+FONT)
    if top:a.const(12,top*320);a.alu(3,3,12)
    imm(0x2a,12,3,0); imm(0x27,13,0,1760)
    a.label('clear'); a.store(0,12); imm(0x27,12,12,4); imm(0x27,13,13,-1)
    imm(0x2f,1,13,0); a.branch(4,'clear')
    imm(0x27,5,3,1288); imm(0x27,8,0,0); a.const(4,65535)
    a.label('character'); imm(0x23,11,6,0); imm(0x2f,0,11,0); a.branch(4,'done')
    imm(0x2f,2,11,127); a.branch(4,'advance')
    imm(0x27,12,0,5); a.alu(11,11,12,0x306); a.alu(11,7,11)
    imm(0x27,12,0,0)
    a.label('column'); imm(0x23,15,11,0); imm(0x2e,3,12,1); a.alu(3,5,3)
    imm(0x27,13,0,7)
    a.label('row'); imm(0x29,2,15,1); imm(0x2f,0,2,0); a.branch(4,'blank')
    a.store(4,3,0,op=0x37); a.store(4,3,320,op=0x37)
    a.label('blank'); imm(0x27,3,3,640); imm(0x2e,15,15,0x41)
    imm(0x27,13,13,-1); imm(0x2f,1,13,0); a.branch(4,'row')
    imm(0x27,11,11,1); imm(0x27,12,12,1); imm(0x2f,4,12,5); a.branch(4,'column')
    a.label('advance'); imm(0x27,5,5,12); imm(0x27,6,6,1); imm(0x27,8,8,1)
    imm(0x2f,4,8,24); a.branch(4,'character')
    a.label('done')
    for i,r in enumerate(saved): imm(0x21,r,1,i*4)
    imm(0x27,1,1,40); imm(0x27,11,0,0); a.emit(0x44004800)
    code=a.finish(); assert len(code)<=0x800
    return code


def reference(pixels,enabled,mode):
    """Independent host pixel oracle; no file I/O or device access."""
    data=bytearray(pixels); data[:320*22]=b'\0'*(320*22)
    for i,char in enumerate(TEXT[mode+1 if enabled else 0]):
        for x,column in enumerate(GLYPHS[char]):
            for y in range(7):
                if column & (1<<y):
                    off=(4+y*2)*320+8+i*12+x*2
                    data[off:off+2]=b'\xff\xff'; data[off+320:off+322]=b'\xff\xff'
    return bytes(data)
