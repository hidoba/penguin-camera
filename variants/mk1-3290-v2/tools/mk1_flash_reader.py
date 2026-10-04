"""Guarded native SPI reader, with RAM fill performed on camera (no flash writes)."""
from mk1_usb_flash import Code, SIZE, FLUSH, READ, INVALIDATE

def reader(origin,result,base,length):
    if origin%64 or base%64 or length%16 or not 16<=length<=65536:
        raise ValueError('invalid reader bounds')
    if not 0x020f0000<=base<=0x027fec00-length-128:
        raise ValueError('invalid buffer bounds')
    if base<origin+512 and origin<base+length+128:
        raise ValueError('reader/buffer overlap')
    c=Code(origin)
    # Callback passes r3=result, where request={flash_offset, poison_word}.
    c.emit(0xd7e14ffc); c.emit(0x9c21fff0)
    c.emit(0x84c30000);c.emit(0xd4013000)
    c.emit(0x84e30004);c.emit(0xd4013804)
    c.const(11,SIZE-length);c.emit(0x39<<26|2<<21|6<<16|11<<11);c.branch(4,'deny')
    c.emit(0x29<<26|7<<21|6<<16|15);c.emit(0x2f<<26|1<<21|7<<16);c.branch(4,'deny')
    c.const(3,base)
    for label,end,pattern in [('before',base+64,0xa5a5a5a5),('poison',base+64+length,None),('after',base+128+length,0x5a5a5a5a)]:
        c.const(5,end)
        if pattern is None: c.emit(0x84c10004)
        else: c.const(6,pattern)
        c.label(label);c.emit(0xd4033000);c.emit(0x9c630004)
        c.emit(0x39<<26|1<<21|3<<16|5<<11);c.branch(4,label)
    c.const(3,base);c.const(4,length+128);c.jal(FLUSH)
    c.emit(0x84610000);c.const(4,base+64);c.const(5,length);c.jal(READ)
    c.emit(0xd4015808)
    c.const(3,base);c.const(4,length+128);c.jal(INVALIDATE)
    c.emit(0x85610008);c.branch(0,'done')
    c.label('deny');c.emit(0x9d60ffff)
    c.label('done');c.const(3,result);c.emit(0xd4035800)
    c.emit(0x9c801970);c.emit(0xd4032004)
    c.emit(0x9c210010);c.emit(0x8521fffc);c.emit(0x44004800)
    blob=c.finish()
    if len(blob)>512: raise ValueError('reader overflow')
    return blob
