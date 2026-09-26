#!/usr/bin/env python3
"""PGPK version 2: JPEGs in the stock printer's column-oriented pixel layout.

Logical roll previews remain 384 dots across. Encoded JPEGs are feed×384, so
the native print routine can consume decoder output without a second image
allocation. Feed is padded with white to a multiple of 32 for decoder alignment.
Original photos and the earlier version-1 assets are never overwritten.
"""
import argparse
import io
import json
from pathlib import Path
import zlib
from PIL import Image
from PIL.JpegImagePlugin import get_sampling
from prepare_release_artwork import HEADER,ENTRY,load_rgb,roll_image,encode_jpeg,digest

ROOT=Path(__file__).resolve().parents[1]

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

def prepare(assets,out):
    if out.exists():raise ValueError('output exists')
    prior=json.loads((assets/'manifest.json').read_text());items=[];records=[]
    out.mkdir(parents=True);(out/'jpegs').mkdir();(out/'roll-previews').mkdir()
    for item in prior['penguins']:
        source=Path(item['source'])
        if digest(source.read_bytes())!=item['source_sha256']:raise ValueError('source changed')
        roll,rotated,content=roll_image(load_rgb(source))
        if list(roll.size)!=item['roll_dimensions']:raise ValueError('roll reconstruction mismatch')
        ready=printer_layout(roll);jpeg=encode_jpeg(ready.convert('RGB'),90,2)
        name=f'{item["id"]:02d}';(out/'jpegs'/f'{name}.jpg').write_bytes(jpeg)
        Image.open(io.BytesIO(jpeg)).convert('L').transpose(Image.Transpose.ROTATE_270).save(out/'roll-previews'/f'{name}.png')
        items.append((jpeg,*ready.size))
        records.append({'id':item['id'],'source_sha256':item['source_sha256'],'source':item['source'],
            'jpeg':f'jpegs/{name}.jpg','sha256':digest(jpeg),'bytes':len(jpeg),
            'logical_roll_dimensions':list(roll.size),'encoded_dimensions':list(ready.size),
            'white_feed_padding':ready.width-roll.height,'decode_allocation_bytes':ready.width*384*3//2})
    data=pack(items);validate(data);(out/'penguins-print.pgpack').write_bytes(data)
    report={'version':2,'status':'OFFLINE PRINT-LAYOUT ASSETS; NOT FLASHED',
        'layout':'encoded JPEG[383-x,y] = logical roll[x,y]; white feed padding aligns width to 32',
        'brightness':'unscaled; apply 190/255 after decoding for continuous grayscale',
        'pack_bytes':len(data),'pack_sha256':digest(data),'penguins':records,
        'max_selected_jpeg_plus_decoded_bytes':max(r['bytes']+r['decode_allocation_bytes'] for r in records)}
    (out/'manifest.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='penguins'},indent=2))
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--assets',type=Path,default=ROOT/'analysis/release_artwork_01')
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();prepare(a.assets,a.output)
