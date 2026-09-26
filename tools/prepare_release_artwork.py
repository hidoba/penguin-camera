#!/usr/bin/env python3
"""Prepare replacement resources and roll-oriented penguins OFFLINE. No flashing.

Use a Pillow build with AVIF support for AVIF inputs. Originals stay untouched.
Penguin JPEGs retain full grayscale range for a later selected print algorithm.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import struct
import zlib
from PIL import Image, ImageCms, ImageDraw, ImageFont, ImageOps
from PIL.JpegImagePlugin import get_sampling
from analyze_firmware import EXPECTED_SHA256, parse_resources
from replace_resource import replace

ROOT=Path(__file__).resolve().parents[1]
EXTENSIONS={'.jpg','.jpeg','.png','.avif','.webp','.bmp','.tif','.tiff'}
HEADER=struct.Struct('<4sIII')  # magic, version, count, directory entry size
ENTRY=struct.Struct('<IIHHII') # absolute pack offset, length, width, height, CRC32, codec

def digest(data):return hashlib.sha256(data).hexdigest()

def load_rgb(path):
    with Image.open(path) as opened:
        if getattr(opened,'n_frames',1)!=1:raise ValueError(f'animated/multipage input: {path}')
        im=ImageOps.exif_transpose(opened);im.load()
        profile=im.info.get('icc_profile')
        if profile:
            # Apply the embedded profile before conversion; do not silently
            # treat a wide-gamut export as untagged sRGB.
            alpha=im.getchannel('A') if 'A' in im.getbands() else None
            im=ImageCms.profileToProfile(im.convert('RGB'),ImageCms.ImageCmsProfile(io.BytesIO(profile)),
                                        ImageCms.createProfile('sRGB'),outputMode='RGB')
            if alpha is not None:im.putalpha(alpha)
        rgba=im.convert('RGBA')
        white=Image.new('RGBA',rgba.size,'white')
        white.alpha_composite(rgba)
        return white.convert('RGB')

def encode_jpeg(im,quality,sampling):
    output=io.BytesIO()
    im.save(output,'JPEG',quality=quality,subsampling=sampling,progressive=False,optimize=True)
    return output.getvalue()

def fit_jpeg(im,original,budget):
    sampling=get_sampling(original)
    if sampling not in (0,1,2):raise ValueError('unsupported original JPEG sampling')
    if im.size!=original.size:raise ValueError(f'dimensions must match {original.size}, got {im.size}')
    for quality in range(100,0,-1):
        payload=encode_jpeg(im,quality,sampling)
        if len(payload)<=budget:
            with Image.open(io.BytesIO(payload)) as decoded:
                decoded.load()
                if decoded.layer!=original.layer:raise ValueError('component/sampling mismatch')
            return payload,quality,sampling
    raise ValueError('cannot fit JPEG into original slot')

def roll_image(source,width=384,max_feed=1024):
    # Long axis lies along paper movement; top of original landscape rotates
    # counterclockwise on the roll, so turn the paper clockwise to view it.
    rotated=source.width>source.height
    image=source.transpose(Image.Transpose.ROTATE_90) if rotated else source
    scale=min(width/image.width,max_feed/image.height)
    size=(max(1,round(image.width*scale)),max(1,round(image.height*scale)))
    scaled=image.resize(size,Image.Resampling.LANCZOS).convert('L')
    result=Image.new('L',(width,size[1]),255)
    result.paste(scaled,((width-size[0])//2,0))
    return result,rotated,size

def make_pack(items):
    offset=HEADER.size+ENTRY.size*len(items)
    directory=bytearray();payloads=bytearray()
    for payload,w,h in items:
        directory.extend(ENTRY.pack(offset,len(payload),w,h,zlib.crc32(payload),1))
        payloads.extend(payload);offset+=len(payload)
    return HEADER.pack(b'PGPK',1,len(items),ENTRY.size)+directory+payloads

def validate_pack(data):
    if len(data)<HEADER.size:raise ValueError('truncated header')
    magic,version,count,stride=HEADER.unpack_from(data)
    if (magic,version,stride)!=(b'PGPK',1,ENTRY.size):raise ValueError('invalid pack header')
    expected=HEADER.size+count*stride
    if expected>len(data):raise ValueError('truncated directory')
    for i in range(count):
        off,size,w,h,crc,codec=ENTRY.unpack_from(data,HEADER.size+i*stride)
        if off!=expected or size==0 or off+size>len(data):raise ValueError('invalid range')
        if codec!=1 or w!=384 or not 1<=h<=1024:raise ValueError('invalid format')
        payload=data[off:off+size]
        if zlib.crc32(payload)!=crc:raise ValueError('CRC mismatch')
        with Image.open(io.BytesIO(payload)) as im:
            im.load()
            if im.format!='JPEG' or im.size!=(w,h) or im.mode!='RGB' or im.info.get('progressive'):
                raise ValueError('JPEG mismatch')
            if get_sampling(im)!=2:raise ValueError('JPEG must use 4:2:0')
        expected=off+size
    if expected!=len(data):raise ValueError('trailing data')
    return count

def sheet(items,path,title,columns=4,tile=(260,355)):
    font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',14)
    width,height=tile;rows=(len(items)+columns-1)//columns
    canvas=Image.new('RGB',(width*columns,rows*height+40),'#eeeeee')
    draw=ImageDraw.Draw(canvas);draw.text((10,10),title,font=font,fill='black')
    for n,(im,label) in enumerate(items):
        x=(n%columns)*width;y=(n//columns)*height+40
        preview=ImageOps.contain(im.convert('RGB'),(width-20,height-70),Image.Resampling.LANCZOS)
        canvas.paste(preview,(x+(width-preview.width)//2,y+5))
        draw.multiline_text((x+10,y+height-58),label,font=font,fill='black',spacing=3)
    canvas.save(path)

def prepare(replacements,penguins,out):
    data=(ROOT/'flash_zb25vq32_read1.bin').read_bytes()
    if digest(data)!=EXPECTED_SHA256:raise ValueError('original firmware hash mismatch')
    _,entries=parse_resources(data)
    by_name={f"{e['index']:03d}_{e['offset']:06x}.jpg":e for e in entries
             if data[e['offset']:e['offset']+3]==b'\xff\xd8\xff'}
    files=sorted(p for p in replacements.iterdir() if p.is_file() and not p.name.startswith('.'))
    photos=sorted(p for p in penguins.iterdir() if p.is_file() and not p.name.startswith('.'))
    if not files or not photos:raise ValueError('empty input directory')
    if any(p.name not in by_name for p in files):raise ValueError('unknown replacement filename')
    if any(p.suffix.lower() not in EXTENSIONS for p in photos):raise ValueError('unknown penguin file type')
    if out.exists():raise ValueError('output already exists')
    for folder in ('resources','resource-previews','penguins','penguin-jpegs'):(out/folder).mkdir(parents=True)
    report={'status':'OFFLINE ASSETS ONLY; NOT FLASHABLE OR INSTALLED','original_sha256':EXPECTED_SHA256,
            'replacements':[],'penguins':[],'orientation':'long axis along paper-feed; landscape rotated 90 degrees CCW',
            'image_processing':'full-range grayscale, no curve, no dithering, no crop, alpha composited on white',
            'ignored_hidden_files':[str(p) for folder in (replacements,penguins) for p in folder.iterdir() if p.name.startswith('.')],
            'printer_layout_note':'image x is across 384 dots, y is feed; stock driver adapter must use data[(383-x)*height+y]'}
    preview_items=[]
    for path in files:
        e=by_name[path.name];original=Image.open(io.BytesIO(data[e['offset']:e['offset']+e['size']]))
        image=load_rgb(path)
        payload,quality,sampling=fit_jpeg(image,original,e['size'])
        patched,_=replace(data,e['index'],payload)
        assert patched[:e['offset']]==data[:e['offset']]
        assert patched[e['offset']+e['size']:]==data[e['offset']+e['size']:]
        # Do not emit a misleading partial firmware: save only the validated payload.
        (out/'resources'/path.name).write_bytes(payload)
        decoded=Image.open(io.BytesIO(payload)).convert('RGB')
        decoded.save(out/'resource-previews'/f'{path.stem}.png')
        report['replacements'].append({'source':str(path),'source_sha256':digest(path.read_bytes()),'index':e['index'],
            'output':f'resources/{path.name}','bytes':len(payload),'slot_bytes':e['size'],'quality':quality,
            'dimensions':list(image.size),'subsampling':sampling,'sha256':digest(payload)})
        preview_items.append((decoded,f'{path.name}\nQ{quality}: {len(payload):,} / {e["size"]:,} bytes'))
    sheet(preview_items,out/'replacement-contact-sheet.png','Replacement resources - final encoded JPEGs',3,(330,275))
    preview_items=[];packed=[]
    for n,path in enumerate(photos,1):
        image=load_rgb(path);prepared,rotated,content_size=roll_image(image)
        name=f'{n:02d}'
        prepared.save(out/'penguins'/f'{name}.png')
        # Three-component neutral RGB JPEG matches the known color-JPEG decoder
        # family while retaining luminance for future choice of print effect.
        payload=encode_jpeg(prepared.convert('RGB'),90,2)
        (out/'penguin-jpegs'/f'{name}.jpg').write_bytes(payload)
        decoded=Image.open(io.BytesIO(payload)).convert('L')
        packed.append((payload,*prepared.size))
        report['penguins'].append({'id':n,'source':str(path),'source_sha256':digest(path.read_bytes()),
            'source_oriented_dimensions':list(image.size),'roll_dimensions':list(prepared.size),
            'content_dimensions':list(content_size),'rotation_ccw':90 if rotated else 0,
            'master':f'penguins/{name}.png','jpeg':f'penguin-jpegs/{name}.jpg','bytes':len(payload),
            'quality':90,'sha256':digest(payload)})
        preview_items.append((decoded,f'{name} | {prepared.width} across x {prepared.height} feed\n'
                                     f'{path.stem[:28]}\n'+('Rotated 90 deg CCW' if rotated else 'Kept orientation')))
    sheet(preview_items,out/'penguin-roll-contact-sheet.png','Penguin roll layout - paper feeds downward; grayscale before print effect')
    pack=make_pack(packed);assert validate_pack(pack)==len(photos)
    (out/'penguins.pgpack').write_bytes(pack)
    report.update(pack_bytes=len(pack),pack_sha256=digest(pack),
                  pack_budget_bytes=1048576,pack_fits_1MiB=len(pack)<=1048576)
    if len(pack)>1048576:raise ValueError('pack exceeds provisional 1MiB budget; review manifest inputs')
    (out/'manifest.json').write_text(json.dumps(report,indent=2)+'\n')
    (out/'README.md').write_text('''# Release artwork — OFFLINE, NOT INSTALLED

`resources/` contains six baseline JPEG replacements validated against the
original dimensions, chroma sampling, slot budgets and offline patch bounds.
Resources 006–009 are photo-frame slots; 064 is Goodbye and 065 is Welcome.
The original source files and verified flash backups are unchanged.

`penguins/` contains lossless full-range grayscale masters after EXIF handling,
white alpha compositing and proportional resize. `penguin-jpegs/` contains
baseline 4:2:0 RGB JPEGs (neutral color), quality 90, for embedded storage.
There is no brightness scaling or dithering baked into them: the final print
path must apply the selected effect exactly once (190/255 only for gray mode).

Orientation: width is 384 dots ACROSS the roll, height runs ALONG the roll.
Landscape sources are rotated 90 degrees counterclockwise so their long axis
runs along the feed; portrait sources retain orientation. No cropping or
stretching. Turn the resulting strip clockwise to view a rotated landscape.
Maximum feed length is 1024 rows; extreme ratios get white side margins rather
than cropping. Contact-sheet images are reduced previews, not printer pixels.

`penguins.pgpack` is a new HOST-SIDE asset container, not a firmware image and
not yet understood by stock or patched camera code. Header: little-endian
`<4sIII>` = PGPK, version 1, count, entry size 20. Each `<IIHHII>` entry contains
absolute offset within the pack, JPEG length, width, height, CRC32, codec 1.
Directory followed by contiguous JPEG payloads. The builder validates all
ranges, checksums and decoded dimensions. A bounded firmware reader, placement,
runtime scratch allocation, decoder compatibility at these dimensions and
printer integration still require implementation/hardware tests.

Do not rename any output to DestBin.bin or place it on the camera's card.
No full firmware, USB action, print job or flash write was performed.
''')
    print(json.dumps({'replacements':len(files),'penguins':len(photos),'pack_bytes':len(pack),
                      'under_1MiB':len(pack)<=1048576,'output':str(out)},indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--replacements',type=Path,required=True)
    p.add_argument('--penguins',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();prepare(args.replacements,args.penguins,args.output)
