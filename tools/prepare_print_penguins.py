#!/usr/bin/env python3
"""PGPK version 2: JPEGs in the stock printer's column-oriented pixel layout.

Logical roll previews remain 384 dots across. Encoded JPEGs are feed×384, so
the native print routine can consume decoder output without a second image
allocation. Feed is padded with white to a multiple of 32 for decoder alignment.
Original photos and the earlier version-1 assets are never overwritten.
"""
import io
import zlib
from PIL import Image
from PIL.JpegImagePlugin import get_sampling
from prepare_release_artwork import HEADER, ENTRY


def printer_layout(roll):
    if roll.width!=384 or not 1<=roll.height<=1024:raise ValueError('invalid roll size')
    feed=(roll.height+31)&~31
    padded=Image.new('L',(384,feed),255);padded.paste(roll,(0,0))
    return padded.transpose(Image.Transpose.ROTATE_90)

def pack(items):
    offset=HEADER.size+len(items)*ENTRY.size;directory=bytearray();body=bytearray()
    for jpeg,w,h in items:
        directory.extend(ENTRY.pack(offset,len(jpeg),w,h,zlib.crc32(jpeg),1))
        body.extend(jpeg);offset+=len(jpeg)
    return HEADER.pack(b'PGPK',2,len(items),ENTRY.size)+directory+body

def validate(data):
    if len(data)<HEADER.size:raise ValueError('truncated pack')
    magic,version,count,stride=HEADER.unpack_from(data)
    if (magic,version,stride)!=(b'PGPK',2,ENTRY.size) or not 1<=count<=64:raise ValueError('bad header')
    end=HEADER.size+count*ENTRY.size
    if end>len(data):raise ValueError('truncated directory')
    records=[]
    for i in range(count):
        off,n,w,h,crc,codec=ENTRY.unpack_from(data,HEADER.size+i*ENTRY.size)
        if off!=end or not 4<=n<=131072 or off+n>len(data):raise ValueError('bad range')
        if not 32<=w<=1024 or w%32 or h!=384 or codec!=1:raise ValueError('bad layout')
        jpeg=data[off:off+n]
        if zlib.crc32(jpeg)!=crc:raise ValueError('bad CRC')
        with Image.open(io.BytesIO(jpeg)) as im:
            im.load()
            if im.format!='JPEG' or im.size!=(w,h) or im.info.get('progressive') or get_sampling(im)!=2:
                raise ValueError('bad JPEG')
        records.append((off,n,w,h,crc));end=off+n
    if end!=len(data):raise ValueError('trailing bytes')
    return records

