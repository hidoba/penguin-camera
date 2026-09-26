"""Offline persistent-integration effects variant; not a hardware installer.

Adds bounded 1024-row support with a separate, sufficiently large print scratch
area. Old RAM builders retain their original 672 limits and byte layout.
"""
from responsive_effects_patch import build as previous_build, SIZE as OLD_SIZE, KERNELS, NAMES
from combined_print_patch import transform, TRANSFORM,LUT
from diffusion_target import make_kernel
from ditherista_target import kernel as fast_kernel,HALFTONE4,ONE_D
from menu_target import renderer, data as menu_data, DRAW, PORTRAIT, FONT, TEXT, SIZE
from menu_controller import install_parts

PRINT_SCRATCH=OLD_SIZE
MAX_DIMENSION=1024

def build(base,original):
    if base%64 or not 0x02090000<=base<=0x02200000-SIZE:raise ValueError('invalid allocation')
    old,patches=previous_build(base,original)
    blob=bytearray(SIZE);blob[:len(old)]=old
    targets=tuple(base+off for off in (*KERNELS,HALFTONE4,ONE_D))
    code=transform(base,targets,base+PRINT_SCRATCH,SIZE,MAX_DIMENSION)
    blob[TRANSFORM:LUT]=b'\0'*(LUT-TRANSFORM);blob[TRANSFORM:TRANSFORM+len(code)]=code
    for mode,off in zip(('floyd','atkinson','stucki','cracked'),KERNELS):
        code=make_kernel(mode,base+off,max_dimension=MAX_DIMENSION)
        blob[off:off+len(code)]=code
    for off,one in ((HALFTONE4,False),(ONE_D,True)):
        code=fast_kernel(base,one,MAX_DIMENSION,SIZE);blob[off:off+len(code)]=code
    code=renderer(base);blob[DRAW:DRAW+len(code)]=code
    code=renderer(base,portrait=True);blob[PORTRAIT:PORTRAIT+len(code)]=code
    font,text=menu_data();blob[FONT:FONT+len(font)]=font;blob[TEXT:TEXT+len(text)]=text
    patches+=install_parts(base,original,blob)
    return bytes(blob),patches
