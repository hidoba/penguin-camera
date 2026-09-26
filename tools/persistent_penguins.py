"""Offline combined effects/menu plus flash-backed penguin payload. Not deployable.

Keep legacy/RAM generators byte-identical. This extended build must go through
its own relocation, cold-boot, memory budget and recovery validation gates.
"""
import struct
from persistent_effects_patch import build as effects_build,SIZE as EFFECTS_SIZE,PRINT_SCRATCH,MAX_DIMENSION
from persistent_payload import package as package_payload
from flash_penguin_worker import build as worker_build,SIZE as WORKER_SIZE,INNER
from random_penguin_ram import EVENT
from menu_target import DRAW,PORTRAIT,renderer
from menu_controller import DISPLAY,display
from combined_print_patch import TRANSFORM,LUT,transform
from responsive_effects_patch import KERNELS,SLICED,META,NAMES,ADAPTERS,PREVIEW_SCRATCH
from live_preview_patch import control,CONTROLS,preview,START,TABLES
from preview_labels import LABEL_CODE,LABELS
from photo_print_policy import install_parts
from ditherista_target import HALFTONE4,ONE_D,kernel as fast_kernel
from ui_trace_patch import BIAS

WORKER=EFFECTS_SIZE
SIZE=EFFECTS_SIZE+WORKER_SIZE
PACK_FLASH=0x240000  # fixed, sector-aligned; payload must fit preceding 256 KiB


def build(base,original,pack,ram_autostart=False,*,penguin_corrections=False,regression_fixes=False,ui_cleanup=False,random_timing=False,preview_speed=False,effects_v5=False,frame_slots=None):
    if base%64 or not 0x02090000<=base<=0x02200000-SIZE:raise ValueError('invalid combined allocation')
    if preview_speed and not regression_fixes:raise ValueError('Preview optimization requires regression fixes')
    if random_timing:
        from penguin_random import validate_clock
        validate_clock(original)
    old,patches=effects_build(base,original);blob=bytearray(SIZE);blob[:len(old)]=old
    protected=(base,base+SIZE)
    targets=tuple(base+off for off in (*KERNELS,HALFTONE4,ONE_D))
    code=transform(base,targets,base+PRINT_SCRATCH,SIZE,MAX_DIMENSION)
    blob[TRANSFORM:LUT]=b'\0'*(LUT-TRANSFORM);blob[TRANSFORM:TRANSFORM+len(code)]=code
    for off,code in ((DRAW,renderer(base,protected=protected,allocation_size=SIZE)),
                     (PORTRAIT,renderer(base,portrait=True,protected=protected,allocation_size=SIZE)),
                     (DISPLAY,display(base,protected=protected,allocation_size=SIZE)),
                     (HALFTONE4,fast_kernel(base,False,MAX_DIMENSION,SIZE)),
                     (ONE_D,fast_kernel(base,True,MAX_DIMENSION,SIZE))):
        blob[off:off+len(code)]=code
    blob[WORKER:]=worker_build(base+WORKER,pack,PACK_FLASH,base,base,SIZE,
                             max_power_state=6 if penguin_corrections else 5,
                             rotate180=penguin_corrections,random_timing=random_timing,
                             debounce_ms=1500 if effects_v5 else None)
    matches=[p for p in patches if p['address']==BIAS+0x826e4]
    if len(matches)!=1:raise ValueError('missing activation hook')
    matches[0]['replacement']=struct.pack('<I',base+WORKER+INNER+EVENT).hex()
    matches[0]['label']='native menu event with flash penguin worker'
    # Carry forward the physically verified camera policy. User subsequently
    # requested retaining the earlier cracked approximation as an extra mode.
    patches+=install_parts(base,original,blob)
    for i,off in enumerate(CONTROLS):
        # Keep IDs stable for the print/preview dispatch tables and old builds.
        code=control(base,i,len(NAMES),base+META,excluded_modes=(5,) if ui_cleanup else ())
        blob[off:off+0x180]=b'\0'*0x180;blob[off:off+len(code)]=code
    blob[LABELS+7*32:LABELS+8*32]=b'CRACKED EXPERIMENTAL'.ljust(32,b'\0')
    from capture_workspace import STATE as CAPTURE_WORKSPACE_STATE
    code=preview(base,tuple(base+o for o in (*ADAPTERS,HALFTONE4,ONE_D)),
                 base+PREVIEW_SCRATCH,base+LABEL_CODE,force_print=True,
                 dynamic_frames=True,allocation_size=SIZE,
                 busy_word=base+CAPTURE_WORKSPACE_STATE if regression_fixes else None)
    blob[START:TABLES]=b'\0'*(TABLES-START);blob[START:START+len(code)]=code
    if regression_fixes:
        from capture_workspace import install_parts as capture_parts
        from preview_labels import overlay
        from responsive_effects_patch import WORKER as PREVIEW_WORKER,worker as preview_worker,MODES
        from diffusion_target import make_kernel
        for off,end,code in [(LABEL_CODE,LABEL_CODE+0x800,overlay(base,top=48)),
                             (PREVIEW_WORKER,LABEL_CODE,preview_worker(base,rows=128,stucki_rows=64,packed_words=preview_speed))]+[
            (off,off+0x1000,make_kernel(mode,base+off,sliced=True,max_slice_rows=128,
                                      optimized=preview_speed and mode!='stucki')) for mode,off in zip(MODES,SLICED)]:
            if len(code)>end-off:raise ValueError('Regression code slot overflow')
            blob[off:end]=b'\0'*(end-off);blob[off:off+len(code)]=code
        patches+=capture_parts(base,original,blob)
    if ui_cleanup:
        from preview_labels import overlay
        code=overlay(base,top=48 if regression_fixes else 0,show_gray=False)
        blob[LABEL_CODE:LABEL_CODE+0x800]=b'\0'*0x800
        blob[LABEL_CODE:LABEL_CODE+len(code)]=code
    if effects_v5:
        if not (ui_cleanup and regression_fixes):raise ValueError('effects v5 requires UI cleanup and regression fixes')
        from effects_v5_update import apply as apply_v5
        v5_hooks=apply_v5(base,blob,SIZE,original)
    if ram_autostart:
        old=struct.pack('<I',0x9c600006)
        if original[0x28ac:0x28b0]!=old:raise ValueError('Startup selector changed')
        patches.append({'address':BIAS+0x28ac,'original':old.hex(),
                        'replacement':struct.pack('<I',0x9c600003).hex(),
                        'label':'cold-boot Camera selector; RAM-only hook, flash boot sector untouched'})
    if effects_v5:
        from effects_v5_update import remap_buttons
        patches+=v5_hooks  # appended last: existing hook order unchanged
        patches+=remap_buttons(patches,original,base)  # update 26: edits the 0x2e hook in place
    if frame_slots is not None:
        from frames import hooks as frame_hooks,MAX_HOOKS
        patches+=frame_hooks(original,frame_slots)  # update 36: frames from the frames folder
        if len(patches)>MAX_HOOKS:raise ValueError('too many RAM hooks for the loader')
    return bytes(blob),patches


def package(original,pack,ram_autostart=False,*,penguin_corrections=False,regression_fixes=False,ui_cleanup=False,random_timing=False,preview_speed=False,effects_v5=False,frame_slots=None):
    from prepare_print_penguins import validate
    records=validate(pack)
    payload,meta=package_payload(original,builder=lambda base,rom:build(base,rom,pack,ram_autostart,
                                penguin_corrections=penguin_corrections,regression_fixes=regression_fixes,
                                ui_cleanup=ui_cleanup,random_timing=random_timing,preview_speed=preview_speed,effects_v5=effects_v5,
                                frame_slots=frame_slots),size=SIZE)
    if preview_speed:
        meta['preview_speed']={'optimized_diffusion':['floyd','atkinson','cracked'],
            'eight_pixels_per_pack_iteration':True,'resolution':[320,240],
            'rows_per_callback':128,'callbacks_per_complete_diffusion_frame':2,
            'print_kernels_changed':False,'extra_allocations':False}
    if random_timing:
        from penguin_random import TICK_ADDRESS
        meta['penguin_random']={'source':'firmware uptime sampled on every accepted Random press',
            'address':hex(TICK_ADDRESS),'tick_step_ms':10,'mix':'32-bit avalanche with accumulated state',
            'immediate_repeats':False,'repeat_exception':'single-image collection',
            'calendar_required':False,'dithering_affected':False,'cryptographic':False}
    meta['RAM_autostart_hook']=ram_autostart
    meta['regression_fixes']={'enabled':regression_fixes,'label_top':48 if regression_fixes else 0,
        'diffusion_rows_per_callback':128 if regression_fixes else 32,
        'stucki_rows_per_callback':64 if regression_fixes else 16,
        'photo_jpeg_preview_workspace_loan':regression_fixes,'hardware_validated':False}
    meta['penguin_corrections']={'enabled':penguin_corrections,
        'max_power_state':6 if penguin_corrections else 5,
        'print_rotation_degrees':180 if penguin_corrections else 0,
        'camera_photo_orientation_changed':False}
    if 0x200000+((len(payload)+15)&~15)>PACK_FLASH:raise ValueError('payload overlaps penguin flash pack')
    superseded=('Random penguin currently','preview still binds','cracked mode is',
                'menu display/navigation verified')
    meta['release_blockers']=[x for x in meta['release_blockers'] if not x.startswith(superseded)]
    meta['release_blockers']+=['cold-boot descriptor/heap-validated preview is interpreter-tested; hardware validation pending',
        'algorithm label visibility and physical preview refresh remain unresolved']
    meta['modes']={i:('Cracked (experimental)' if i==6 else name) for i,name in enumerate(NAMES)
                   if not (ui_cleanup and i==5)}
    if ui_cleanup:
        meta['ui_cleanup']={'stucki_selectable':False,'normal_preview_label':False,
                            'normal_print_brightness_scale':'190/255','mode_ids_renumbered':False}
    if effects_v5:
        from effects_v5_update import metadata
        meta.update(metadata())
    if frame_slots is not None:
        meta['frames']={'count':len(frame_slots),'resources':list(frame_slots)}
    meta['cracked_disabled']=False
    meta['cracked_status']='existing approximation retained by explicit user request; not an exact Ditherboy match'
    meta['always_print']=True
    meta['normal_and_dithered_camera_RAM_print_user_verified']=True
    meta['preview_binding']='fixed firmware descriptor objects; dynamic aligned pixels with exact allocated heap ownership and shape checks'
    meta['release_blockers'].append('flash-backed worker is interpreter-tested only; combined hardware/cold-boot test pending')
    meta['penguin_worker']={'offset':WORKER,'bytes':WORKER_SIZE,'flash_pack_offset':PACK_FLASH,
        'images':len(records),'format':'PGPK v2 printer-column JPEGs',
        'max_selected_jpeg_allocation':max((r[1]+15)&~15 for r in records),
        'max_selected_jpeg_plus_decoded':max(((r[1]+15)&~15)+r[2]*r[3]*3//2 for r in records),
        'checks':'compiled exact JPEG CRC32, bounds, allocator/driver guards; no automatic retries',
        'hardware_flash_worker_verified':False,'resident_RAM_subset_button_print_user_verified':True}
    return payload,meta
