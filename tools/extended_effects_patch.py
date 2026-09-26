"""Six-mode RAM prototype builder. Fresh allocation only, never flash directly."""
from combined_print_patch import build as build_combined, transform, TRANSFORM, LUT
from live_preview_patch import preview, control, START, TABLES, CONTROLS
from diffusion_target import make_kernel

SIZE=0xb000
KERNELS=(0x5000,0x6000,0x7000)
PREVIEW_SCRATCH=0x8000   # 320*3*4 = 3840 bytes
PRINT_SCRATCH=0x9000     # 672*3*4 = 8064 bytes
NAMES=('Bayer 8x8','Bayer 4x4','Threshold','Floyd–Steinberg','Atkinson','Stucki')


def build(base,original):
    if base%64 or not 0x02090000<=base<=0x02200000-SIZE:
        raise ValueError('Unapproved extended allocation')
    previous,patches=build_combined(base,original)
    blob=bytearray(SIZE); blob[:len(previous)]=previous
    targets=tuple(base+off for off in KERNELS)
    for mode,off in zip(('floyd','atkinson','stucki'),KERNELS):
        code=make_kernel(mode,base+off); blob[off:off+len(code)]=code
    code=preview(base,targets,base+PREVIEW_SCRATCH)
    blob[START:TABLES]=b'\0'*(TABLES-START); blob[START:START+len(code)]=code
    code=transform(base,targets,base+PRINT_SCRATCH,SIZE)
    blob[TRANSFORM:LUT]=b'\0'*(LUT-TRANSFORM); blob[TRANSFORM:TRANSFORM+len(code)]=code
    for i,off in enumerate(CONTROLS):
        code=control(base,i,len(NAMES)); blob[off:off+0x180]=b'\0'*0x180
        blob[off:off+len(code)]=code
    return bytes(blob),patches
