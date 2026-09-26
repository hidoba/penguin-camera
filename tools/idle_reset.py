"""Auto power-off counts from the last press of ANY button (update 23).

Stock auto-off (flash 0xf270): every >=500 ms the idle counter 0x02085f34 grows
by 500 and the camera powers off (mode 1) when it reaches setting x 1000 ms. The
counter is reset only by the global event handlers 0x8c88..0x8eb8, registered for
events 0x1c 0x1e 0x20 0x24 0x26 0x2c 0x2d 0x30. The shutter (0x29, queued
capture 0x2a), the print/save toggle (0x2e) and 0x2b have no such handler, so
taking photos never postponed auto-off.

Hook: the key dispatcher 0x8700.. converges at flash 0x87c0 (l.movhi r4,0x208)
with r2 = event (validated <= 48) and 4(r1) = press subtype (0 fresh press,
2 release; the same word handlers receive via r5). The trampoline clears the
idle counter on a fresh press of any physical event 0x1c..0x30 (the same state
0xf270(1) produces), redoes the replaced instruction and returns to 0x87c4.
Only r4 and the flag change; after 0x87c0 stock sets r4 and does not read the flag
before its own compare at 0x87d4.
"""
import struct
from live_preview_patch import Code
from ui_trace_patch import BIAS
from build_gray_candidate import branch

IDLE_CODE = 0xab00             # update 28 (0xce80 in 27, 0xe880 in 24-26, 0xe900 in 23)
MAX_CODE = 0x100                # up to ORANGE 0xac00
DISPATCH = 0x87c0              # flash offset
ORIGINAL = 0x18800208          # l.movhi r4,0x208
IDLE_COUNTER = 0x02085f34
FIRST_KEY, LAST_KEY = 0x1c, 0x30
OK_EVENT = 0x1c
MODE_WORD = 0x02085e9c
CAMERA_MODE = 3
MENU_MODE = 6
SHUTTER_EVENT, BACK_EVENT = 0x29, 0x30
MENU_SELECTION = 0x02089334     # 0 camera, 1 settings, 2 random penguin
MENU_ACTIVATE_SLOT = 0x020802e4 # menu event table: handler for 0x1c (activate)


def trampoline(base, curve_state=None, curves=4, menu_remap=False):
    """Runs at 0x87c0 for every validated event: r2 = event, 4(r1) = subtype.
    Never changes r2/r11. Uses r4/r12/r13; the menu activate call (fresh shutter /
    4th button in the menu) may also clobber the other caller-saved registers, which
    are dead at 0x87c0 (the dispatcher reloads r3/r4/r5/r14 before use)."""
    a = Code(); imm = a.immediate; origin = base+IDLE_CODE
    imm(0x2f, 4, 2, FIRST_KEY); a.branch(4, 'back')          # sfltui r2,0x1c
    imm(0x2f, 2, 2, LAST_KEY); a.branch(4, 'back')           # sfgtui r2,0x30
    imm(0x21, 4, 1, 4); imm(0x2f, 1, 4, 0); a.branch(4, 'not_fresh')
    a.const(12, IDLE_COUNTER); a.store(0, 12, 0)
    a.label('not_fresh')
    if curve_state is not None or menu_remap:
        a.const(12, MODE_WORD); imm(0x21, 12, 12, 0)
    if curve_state is not None:
        # Update 25: OK (0x1c) in camera mode cycles the gray curve 0..curves.
        imm(0x2f, 1, 12, CAMERA_MODE); a.branch(4, 'not_camera')
        imm(0x2f, 1, 2, OK_EVENT); a.branch(4, 'back'); imm(0x2f, 1, 4, 0); a.branch(4, 'back')
        a.const(12, base+curve_state); imm(0x21, 13, 12, 0); imm(0x27, 13, 13, 1)
        imm(0x2f, 5, 13, curves); a.branch(4, 'curve_ok'); imm(0x27, 13, 0, 0)
        a.label('curve_ok'); a.store(13, 12, 0); a.branch(0, 'back')
        a.label('not_camera')
    if menu_remap:
        # Menu (update 32): on a fresh shutter (0x29) or 4th-button (0x30) press call
        # the menu's own activate handler (same as OK: camera / settings / random
        # penguin). The 4th button selects Camera first. The event itself is then
        # dispatched unchanged, so the stock global handlers still run for it.
        imm(0x2f, 1, 12, MENU_MODE); a.branch(4, 'back')
        imm(0x2f, 1, 4, 0); a.branch(4, 'back')                        # fresh press only
        imm(0x2f, 0, 2, SHUTTER_EVENT); a.branch(4, 'activate')
        imm(0x2f, 1, 2, BACK_EVENT); a.branch(4, 'back')
        a.const(12, MENU_SELECTION); a.store(0, 12, 0, op=0x36)
        a.label('activate')
        a.const(12, MENU_ACTIVATE_SLOT); imm(0x21, 12, 12, 0); imm(0x2f, 0, 12, 0); a.branch(4, 'back')
        imm(0x27, 5, 1, 4)                                               # -> the dispatcher's subtype word (0)
        imm(0x27, 1, 1, -8); a.store(11, 1, 0); a.store(2, 1, 4)
        imm(0x27, 3, 0, 0); imm(0x27, 4, 0, 1); a.emit(0x48000000 | 12 << 11)   # l.jalr r12
        imm(0x21, 11, 1, 0); imm(0x21, 2, 1, 4); imm(0x27, 1, 1, 8)
    a.label('back')
    a.emit(ORIGINAL)
    code = a.finish()
    code += branch(origin+len(code), BIAS+DISPATCH+4)            # l.j 0x87c4
    if len(code) > MAX_CODE: raise ValueError('idle reset overflow')
    return code


def hook(original, base):
    old = original[DISPATCH:DISPATCH+4]
    if old != struct.pack('<I', ORIGINAL): raise ValueError('unexpected key dispatcher instruction')
    return {'address': BIAS+DISPATCH, 'original': old.hex(),
            'replacement': branch(BIAS+DISPATCH, base+IDLE_CODE).hex(),
            'label': 'key dispatcher: any fresh button press resets the auto power-off counter'}
