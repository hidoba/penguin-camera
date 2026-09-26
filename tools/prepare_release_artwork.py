#!/usr/bin/env python3
"""Prepare replacement resources and roll-oriented penguins OFFLINE. No flashing.

Use a Pillow build with AVIF support for AVIF inputs. Originals stay untouched.
Penguin JPEGs retain full grayscale range for a later selected print algorithm.
"""
import hashlib
import io
from pathlib import Path
import struct
import zlib
from PIL import Image, ImageCms, ImageDraw, ImageFont, ImageOps
from PIL.JpegImagePlugin import get_sampling

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


