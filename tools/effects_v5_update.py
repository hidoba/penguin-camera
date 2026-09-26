"""Update-05 effect changes applied on top of the integration-17 payload.

- New stable IDs 9 (Bayer 16x16 until update 34; HALFTONE 5X5 since update 35) and
  10 (HALFTONE 6X6).
- Threshold (ID 2) and Stucki (ID 5) are skipped by previous/next; their code
  and table remain so no dispatch index moves.
- Magic 4x4 45 uses a gamma-0.8 lifted threshold table (0 stays black).
"""
from live_preview_patch import control, CONTROLS, preview, START, TABLES
from combined_print_patch import transform, TRANSFORM
from photo_print_policy import SHUTTER
from date_stamp import stamp, font as stamp_font, STAMP, STAMP_FONT, SETTING, disable_stock_hook, disable_stock_flag_hook, disable_print_capture_flag_hooks
from color_gray import orange, ORANGE, MAX_CODE as ORANGE_MAX, WEIGHTS
import tone_gray
from responsive_effects_patch import KERNELS, ADAPTERS, PREVIEW_SCRATCH, META, NAMES
from persistent_effects_patch import PRINT_SCRATCH, MAX_DIMENSION
from ditherista_target import MAGIC, ONE_D, MAGIC_TABLE
from ditherista_tables import THRESHOLDS
from preview_labels import overlay, LABEL_CODE, LABELS
from osd_label import osd_label, SHORT_NAMES, flip_wrapper, flip_hook, FLIP_WRAP
from live_preview_patch import STATE as PREVIEW_STATE
from capture_workspace import STATE as CAPTURE_WORKSPACE_STATE
from effects_v5 import magic_thresholds, bayer8_thresholds, EDGE_THRESHOLD, MAGIC_GAMMA, MAGIC_LOWEST
from effects_v5_target import (halftone_kernel, halftone_table, HALFTONE, HALFTONE_TABLE, REGION,
                               HALFTONE5, HALFTONE5_TABLE, HALFTONE5_END, HALFTONE5_TAPS)
import idle_reset
import gray_filters
import osd_screen

V5_NAMES = (*NAMES, 'Halftone 5x5', 'Halftone 6x6')
V5_LABELS = {9: b'HALFTONE 5X5', 10: b'HALFTONE 6X6'}   # cosine clustered-dot screens
EXCLUDED = (2, 5, 7)   # update 35: Magic 4x4 (HALFTONE 4X4) replaced by HALFTONE 5X5 (mode 9)


def apply(base, blob, size, original=None):
    lo, hi = REGION
    if any(blob[lo:hi]): raise ValueError('update-05 code region is not free')
    if blob[TABLES:TABLES+64] != bayer8_thresholds(): raise ValueError('Bayer table mismatch')
    if blob[MAGIC_TABLE:MAGIC_TABLE+16] != THRESHOLDS: raise ValueError('Magic table mismatch')
    if any(blob[HALFTONE5:HALFTONE5_END]): raise ValueError('halftone 5x5 region is not free')
    code = halftone_kernel(base, MAX_DIMENSION, size, 5, HALFTONE5_TAPS, HALFTONE5_TABLE)
    if len(code) > HALFTONE5_TABLE-HALFTONE5: raise ValueError('halftone 5x5 overflow')
    for off, code in ((HALFTONE, halftone_kernel(base, MAX_DIMENSION, size)),
                      (HALFTONE_TABLE, halftone_table()),
                      (HALFTONE5, code), (HALFTONE5_TABLE, halftone_table(5))):
        blob[off:off+len(code)] = code
    blob[MAGIC_TABLE:MAGIC_TABLE+16] = magic_thresholds()
    if any(blob[STAMP:STAMP_FONT+64]): raise ValueError('date stamp region is not free')
    code = stamp(base, setting=SETTING if original is not None else None); blob[STAMP:STAMP+len(code)] = code
    if any(blob[ORANGE:ORANGE+ORANGE_MAX]): raise ValueError('colour conversion region is not free')
    code = orange(base, size); blob[ORANGE:ORANGE+len(code)] = code
    # Update 13: measured gray tone curve + line-load + heat-history compensation
    if any(blob[tone_gray.TONE:tone_gray.COL_TABLE+768]): raise ValueError('tone region is not free')
    tones = tone_gray.tables()
    code = tone_gray.native(base, tones); blob[tone_gray.TONE:tone_gray.TONE+len(code)] = code
    for off, data in tone_gray.blob_tables(tones).items(): blob[off:off+len(data)] = data
    blob[STAMP_FONT:STAMP_FONT+55] = stamp_font()
    # Update 24/25: gray preview + OK-button curves (preview, dither preview and every print).
    if any(blob[gray_filters.CODE:gray_filters.CODE_END]) or any(blob[gray_filters.LUTS:gray_filters.DATA_END]):
        raise ValueError('gray filter region is not free')
    parts, gray_entries = gray_filters.build(base, base+ORANGE)
    for off, data in parts.items(): blob[off:off+len(data)] = data
    # Update 19's smaller slices / sliced halftone / preview conversion skip were
    # rolled back in update 20 (lower preview fps and a flashing, not-black top
    # bar on the camera). Only the faster, bit-exact colour conversion is kept.
    extra = (base+HALFTONE5, base+HALFTONE)
    code = transform(base, tuple(base+o for o in (*KERNELS, MAGIC, ONE_D))+extra,
                     base+PRINT_SCRATCH, size, MAX_DIMENSION, after=base+STAMP, before=base+gray_entries['print'], gray=base+tone_gray.TONE,
                     reject_flush=True)
    # Only the transform's own slot: the always-print shutter/capture wrappers
    # (0x800, 0xa00) and diagnostics (0xf00) follow it and must survive.
    if len(code) > SHUTTER-TRANSFORM: raise ValueError('transform overflows into policy wrappers')
    blob[TRANSFORM:SHUTTER] = b'\0'*(SHUTTER-TRANSFORM); blob[TRANSFORM:TRANSFORM+len(code)] = code
    code = preview(base, tuple(base+o for o in (*ADAPTERS, MAGIC, ONE_D))+extra,
                   base+PREVIEW_SCRATCH, base+LABEL_CODE, force_print=True,
                   dynamic_frames=True, allocation_size=size,
                   busy_word=base+CAPTURE_WORKSPACE_STATE, pre=base+gray_entries['pre'],
                   normal=base+gray_entries['normal'], reject_flush=True)
    blob[START:TABLES] = b'\0'*(TABLES-START); blob[START:START+len(code)] = code
    for i, off in enumerate(CONTROLS):
        code = control(base, i, len(V5_NAMES), base+META, excluded_modes=EXCLUDED)
        blob[off:off+0x180] = b'\0'*0x180; blob[off:off+len(code)] = code
    # Update 12: the name goes into the stock top icon bar (UI overlay layer),
    # not into the photo; same call site and ABI as the old in-frame overlay.
    # Update 25: whole camera-screen overlay (curve name top, dither name bottom, clock,
    # button hints) painted by osd_screen from the preview callback and the overlay flip.
    if any(blob[osd_screen.OSD_CODE:osd_screen.OSD_END]): raise ValueError('osd screen region is not free')
    if any(blob[osd_screen.ICON_DATA:osd_screen.ICON_END]): raise ValueError('icon region is not free')
    if any(blob[osd_screen.ICON_DATA2:osd_screen.ICON2_END]): raise ValueError('icon region 2 is not free')
    parts, paint = osd_screen.build(base, gray_filters.CURVE_STATE, PREVIEW_STATE, len(V5_NAMES)-1, size,
                                    gray_filters.LABEL_MODE0, gray_filters.curve_lut_offsets(), gray_filters.CURVE_COUNT)
    for off, data in parts.items(): blob[off:off+len(data)] = data
    blob[LABEL_CODE:LABEL_CODE+0x800] = b'\0'*0x800
    code = osd_screen.screen_entry(base, base+paint); blob[LABEL_CODE:LABEL_CODE+len(code)] = code
    code = osd_screen.flip_wrapper(base, base+paint); blob[FLIP_WRAP:FLIP_WRAP+len(code)] = code
    for off, data in osd_screen.font_patch().items():
        if any(blob[off:off+len(data)]): raise ValueError('font slot in use')
        blob[off:off+len(data)] = data
    for mode, short in SHORT_NAMES.items():
        blob[LABELS+(mode+1)*32:LABELS+(mode+2)*32] = short.encode().ljust(32, b'\0')
    for mode, text in V5_LABELS.items():
        off = LABELS+(mode+1)*32
        if any(blob[off:off+32]): raise ValueError('label slot in use')
        blob[off:off+32] = text.ljust(32, b'\0')
    # Update 32: curve state s (0 AUTO LEVELS .. 4) -> slot 12+s; NO DITHERING -> slot 17.
    for slot, text in ([(gray_filters.LABEL_MODE0+1+state, t) for state, t in enumerate(gray_filters.CURVE_NAMES)]
                       + [(osd_screen.NO_DITHER_SLOT, osd_screen.NO_DITHER_NAME)]):
        off = LABELS+slot*32
        if any(blob[off:off+32]): raise ValueError('label slot in use')
        blob[off:off+32] = text.encode().ljust(32, b'\0')
    for slot in range(1, 12):     # dither names + up/down glyph must fit the bottom bar
        text = bytes(blob[LABELS+slot*32:LABELS+slot*32+32]).split(b'\0')[0]
        if osd_screen.DITHER_NAME[0]+12*len(text)+osd_screen.UPDOWN_GAP > osd_screen.UPDOWN_MAX_X:
            raise ValueError(f'dither name too long for the bottom bar: {text!r}')
    if any(blob[LABELS+18*32:LABELS+0x400]): raise ValueError('label table overflow')
    # Update 31: baby penguin in the top-right corner of the native menu.
    import menu_penguin
    from menu_controller import display as menu_display, DISPLAY as MENU_DISPLAY, PREVIOUS as MENU_PREVIOUS
    for off, data in list(menu_penguin.chunks()[0].items())+[(menu_penguin.CODE, menu_penguin.blit(base))]:
        if any(blob[off:off+len(data)]): raise ValueError('menu penguin region is not free')
        blob[off:off+len(data)] = data
    code = menu_display(base, protected=(base, base+size), allocation_size=size, after_draw=base+menu_penguin.CODE)
    if len(code) > MENU_PREVIOUS-MENU_DISPLAY: raise ValueError('menu display overflow')
    blob[MENU_DISPLAY:MENU_PREVIOUS] = b'\0'*(MENU_PREVIOUS-MENU_DISPLAY); blob[MENU_DISPLAY:MENU_DISPLAY+len(code)] = code
    # Update 32: "frame 0 -> none" stub (gray filter code area, zero-checked above).
    code = gray_filters.up_exit_stub(base)
    if any(blob[gray_filters.UP_EXIT_STUB:gray_filters.UP_EXIT_STUB+len(code)]): raise ValueError('stub slot in use')
    blob[gray_filters.UP_EXIT_STUB:gray_filters.UP_EXIT_STUB+len(code)] = code
    # Update 32: photo JPEG workspace = both preview banks + WORK_IMAGE (101376 bytes,
    # above the stock 81920-byte re-encode threshold); preview is stopped during capture.
    import capture_workspace as cw
    for slot, make in ((cw.ALLOC, cw.allocator), (cw.CLEANUP, cw.cleanup)):
        code = make(base, cw.LOAN_START, cw.LOAN_BYTES)
        blob[slot:slot+0x400] = b'\0'*0x400; blob[slot:slot+len(code)] = code
    # Update 23: every fresh button press (incl. shutter / print toggle) restarts auto power-off.
    lo = idle_reset.IDLE_CODE
    if any(blob[lo:lo+idle_reset.MAX_CODE]): raise ValueError('idle reset region is not free')
    code = idle_reset.trampoline(base, gray_filters.CURVE_STATE, gray_filters.CURVE_COUNT, menu_remap=True); blob[lo:lo+len(code)] = code
    return ([disable_stock_hook(original), disable_stock_flag_hook(original)]
            + disable_print_capture_flag_hooks(original) + [flip_hook(original, base)]
            + [idle_reset.hook(original, base), gray_filters.sensor_effect_hook(original)]
            + gray_filters.filter_skip_hooks(original, base) + [osd_screen.stock_clock_hook(original)]) if original is not None else []


# Update 26: left buttons, top to bottom: OK = curve, 2nd = switch camera, 3rd = menu,
# 4th = dithering on/off. Camera-mode event table (RAM 0x207e1d8, flash 0x805d8):
# 0x2e (2nd button) was our dither toggle, 0x26 (3rd) stock switch camera 0xb880,
# 0x30 (4th) stock menu 0xb7c0 (-> 0x908c(6)).
CAMERA_TABLE_2E, CAMERA_TABLE_26, CAMERA_TABLE_30 = 0x0207e224, 0x0207e204, 0x0207e21c
STOCK_SWITCH_CAMERA, STOCK_MENU = 0x02009480, 0x020093c0


MENU_INIT_CLAMP = 0x020159e8          # 'sfleui r4,N' before menu init resets selection (+108)


def remap_buttons(patches, original, base):
    """In-place on the hook list: 2nd button -> stock switch camera, 3rd -> stock
    menu, 4th -> our dither toggle (CONTROLS[0]). Update 32: the menu always opens
    on Camera (selection reset to 0 on menu entry), so a habitual shutter press in
    the menu never prints a penguin by accident."""
    import struct
    from ui_trace_patch import BIAS as B
    word = lambda addr: struct.unpack_from('<I', original, addr-B)[0]
    if (word(CAMERA_TABLE_26), word(CAMERA_TABLE_30)) != (STOCK_SWITCH_CAMERA, STOCK_MENU):
        raise ValueError('unexpected camera event table')
    toggle = [p for p in patches if p['address'] == CAMERA_TABLE_2E]
    if len(toggle) != 1 or toggle[0]['replacement'] != struct.pack('<I', base+CONTROLS[0]).hex():
        raise ValueError('dither toggle hook not found')
    toggle[0]['replacement'] = struct.pack('<I', STOCK_SWITCH_CAMERA).hex()
    toggle[0]['label'] = '2nd button (0x2e): stock switch camera (update 26)'
    clamp = [p for p in patches if p['address'] == MENU_INIT_CLAMP]
    if len(clamp) != 1 or clamp[0]['replacement'] != struct.pack('<I', 0xbca40002).hex():
        raise ValueError('menu init hook not found')
    clamp[0]['replacement'] = struct.pack('<I', 0xbca40000).hex()   # sfleui r4,0: any selection -> 0
    clamp[0]['label'] = 'menu init: selection always Camera (update 32)'
    return [{'address': CAMERA_TABLE_26, 'original': struct.pack('<I', STOCK_SWITCH_CAMERA).hex(),
             'replacement': struct.pack('<I', STOCK_MENU).hex(), 'label': '3rd button (0x26): stock menu'},
            {'address': CAMERA_TABLE_30, 'original': struct.pack('<I', STOCK_MENU).hex(),
             'replacement': struct.pack('<I', base+CONTROLS[0]).hex(), 'label': '4th button (0x30): dither toggle'}]


def metadata():
    return {'modes': {i: n for i, n in enumerate(V5_NAMES) if i not in EXCLUDED},
            'effects_v5': {'new_modes': {9: 'Halftone 5x5: [1 7 16 7 1]/32 blur (sigma 0.83) vs 5x5 cosine screen (update 35)',
                                         10: 'Halftone 6x6: binomial sigma-1 blur vs 6x6 cosine screen'},
                           'skipped_modes': list(EXCLUDED),
                           'magic_input_gamma': MAGIC_GAMMA, 'magic_lowest_threshold': MAGIC_LOWEST,
                           'magic_thresholds': list(magic_thresholds()),
                           'mode_ids_renumbered': False,
                           'gray_print': 'measured tone curve + line-load + heat-history + head-position compensation (tone_gray.py, model analysis/gray_model_01.json); 190/255 LUT only as non-384 fallback',
                           'gray_conversion': 'strong orange filter 0.55R+0.40G+0.05B via Y+0.4855(Cr-128)-0.0491(Cb-128); print (all modes) and dither preview',
                           'mode_label': 'stock top icon bar overlay layer (0x020869a0), after the camera pictogram; not drawn into the photo',
                           'date_stamp_setting': 'stock settings entry 17 (0x02081b04 == 0x81000015); stock renderer 0x54ebc disabled by RAM hook',
                           'date_stamp': 'YYYY-MM-DD bottom-left, 5x7 font x3, 2-dot white stroke, every photo print; source RTC 0x02086a0c'}}
