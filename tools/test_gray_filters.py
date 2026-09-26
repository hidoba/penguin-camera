import random
import struct
import unittest
from pathlib import Path
from or1k_subset import CPU
import gray_filters as gf
import color_gray as cg
import idle_reset as ir
from ui_trace_patch import BIAS

ROOT = Path(__file__).resolve().parents[1]
BASE = 0x02147240
SIZE = 0x30400
FRAME = 0x02099e00
N = 320*240


def frame(seed, flat=False, w=320, h=240):
    rnd = random.Random(seed)
    n = w*h
    if flat:   # low-contrast scene for auto levels
        y = bytes(100+rnd.randrange(40) for _ in range(n))
    else:
        y = bytes((x*7+yy*3+rnd.randrange(60)) % 256 for yy in range(h) for x in range(w))
    return bytearray(y+bytes(rnd.randrange(256) for _ in range(n//2)))


MASK = 0x020d0000                # frame luma buffer used by the stock compositor (test copy)


def setup(curve, buf, buf_base, frame=None, display_lut=None):
    """frame: None (no stock frame) or 320x240 frame luma (active, category 3)."""
    blob = bytearray(SIZE)
    code = cg.orange(BASE, SIZE); blob[cg.ORANGE:cg.ORANGE+len(code)] = code
    parts, entries = gf.build(BASE, BASE+cg.ORANGE)
    for off, data in parts.items(): blob[off:off+len(data)] = data
    struct.pack_into('<I', blob, gf.CURVE_STATE, curve)
    if display_lut is not None: blob[gf.DISPLAY_LUT:gf.DISPLAY_LUT+256] = display_lut
    fs = bytearray(0x100); cat = bytearray(0x40); regions = []
    if frame is not None:
        struct.pack_into('<I', fs, gf.FRAME_STATE-0x0208a900, 1)
        struct.pack_into('<HH', fs, gf.FRAME_STATE-0x0208a900+28, 320, 240)
        struct.pack_into('<I', fs, gf.FRAME_STATE-0x0208a900+36, MASK)
        cat[gf.FILTER_CATEGORY-0x02089300] = 3
        regions.append((MASK, bytearray(frame), False))
    cpu = CPU([(BASE, blob, True), (buf_base, buf, True), (0x021ffc00, bytearray(0x400), True),
               (0x0208a900, fs, False), (0x02089300, cat, False)]+regions)
    cpu.r = [0x7000+i for i in range(32)]; cpu.r[0] = 0; cpu.r[1] = 0x021fff00
    return cpu, blob, entries


CURVES = (0, 1, 2, 3, 4, 5, 0xffffffff)          # 0 AUTO, 1 FLAT, 2..4 LUT curves, rest ignored


class GrayFilterTests(unittest.TestCase):
    def test_curves(self):
        self.assertEqual(gf.CURVE_NAMES[:2], ('AUTO LEVELS', 'FLAT CURVE')); self.assertNotIn('LESS CONTRAST', gf.CURVE_NAMES)
        self.assertEqual((gf.AUTO, gf.FLAT), (0, 1))    # boot default (zeroed state word) = AUTO LEVELS
        for c in gf.curves():
            self.assertEqual(len(c), 256); self.assertTrue(all(c[i] <= c[i+1] for i in range(255)))

    def test_normal_preview_matches_reference(self):
        for curve in CURVES:
            for flat in ((False, True) if curve == gf.AUTO else (False,)):
                buf = frame(curve % 7, flat)
                want = gf.preview_reference(buf, curve)
                cpu, blob, entries = setup(curve, buf, FRAME)
                cpu.r[3] = FRAME; before = cpu.r.copy()
                cpu.run(BASE+entries['normal'], limit=10_000_000)
                self.assertEqual(bytes(buf), want, (curve, flat))
                self.assertEqual(cpu.r[11], 0)
                self.assertEqual(cpu.r[1:9]+cpu.r[10:11]+cpu.r[12:], before[1:9]+before[10:11]+before[12:])

    def test_dither_pre_and_print_match_reference(self):
        for name, w, h, step in (('pre', 320, 240, gf.PREVIEW_STEP), ('print', 384, 96, gf.PRINT_STEP),
                                 ('print', 512, 384, gf.PRINT_STEP)):
            for curve in CURVES:
                buf = frame(w+curve % 7, curve == gf.AUTO, w, h)
                want = gf.orange_curve_reference(buf, w, h, curve, step)
                chroma = bytes(buf[w*h:])
                cpu, blob, entries = setup(curve, buf, 0x020c0000 if name == "print" else FRAME)
                cpu.r[3:6] = [0x020c0000 if name == "print" else FRAME, w, h]; before = cpu.r.copy()
                cpu.run(BASE+entries[name], limit=30_000_000)
                self.assertEqual(bytes(buf[:w*h]), want, (name, w, curve))
                self.assertEqual(bytes(buf[w*h:]), chroma)                          # chroma untouched
                self.assertEqual(cpu.r[1:9]+cpu.r[10:], before[1:9]+before[10:])    # every register

    def test_frame_pixels_excluded_from_auto_levels(self):
        rnd = random.Random(5)
        # frame: opaque (Y 200..255) on a 40-px border, transparent (<= 27) inside
        frame_luma = bytes(255 if (x < 40 or x >= 280 or y < 40 or y >= 200) else rnd.randrange(28)
                           for y in range(240) for x in range(320))
        buf = frame(3, True)
        want = gf.preview_reference(buf, gf.AUTO, frame_luma)
        self.assertNotEqual(want, gf.preview_reference(buf, gf.AUTO))          # the mask matters
        cpu, blob, entries = setup(gf.AUTO, buf, FRAME, frame_luma)
        cpu.r[3] = FRAME; cpu.run(BASE+entries['normal'], limit=10_000_000)
        self.assertEqual(bytes(buf), want)
        # the copy for the curve icon / prints is the photo-only table
        photo = bytes(want[:0])  # (content checked through the print test below)
        # frame covering everything: fewer than 256 photo samples -> picture left as converted
        buf = frame(4, True); cover = bytes([255])*(320*240)
        want = gf.preview_reference(buf, gf.AUTO, cover)
        cpu, blob, entries = setup(gf.AUTO, buf, FRAME, cover)
        cpu.r[3] = FRAME; cpu.run(BASE+entries['normal'], limit=10_000_000)
        self.assertEqual(bytes(buf), want)

    def test_print_with_frame_uses_preview_table(self):
        lut = bytes(min(255, v*2) for v in range(256))
        w, h = 384, 96
        buf = frame(9, True, w, h)
        want = gf.orange_curve_reference(buf, w, h, gf.AUTO, gf.PRINT_STEP, lut=lut)
        cpu, blob, entries = setup(gf.AUTO, buf, 0x020c0000, bytes(320*240), display_lut=lut)
        cpu.r[3:6] = [0x020c0000, w, h]; cpu.run(BASE+entries['print'], limit=30_000_000)
        self.assertEqual(bytes(buf[:w*h]), want)
        # without a frame the print measures itself
        buf = frame(9, True, w, h)
        cpu, blob, entries = setup(gf.AUTO, buf, 0x020c0000, None, display_lut=lut)
        cpu.r[3:6] = [0x020c0000, w, h]; cpu.run(BASE+entries['print'], limit=30_000_000)
        self.assertEqual(bytes(buf[:w*h]), gf.orange_curve_reference(frame(9, True, w, h), w, h, gf.AUTO, gf.PRINT_STEP))

    def test_auto_levels_stretches_flat_scene(self):
        y = bytes(100+(i % 40) for i in range(N))
        lut = gf.auto_lut(y[::4])
        self.assertEqual(lut[86], 0); self.assertEqual(lut[150], 255)   # 36-level scene: 64-level minimum range
        full = bytes(i % 256 for i in range(N))
        self.assertLessEqual(abs(gf.auto_lut(full[::4])[128]-128), 2)

    def test_hooks(self):
        original = (ROOT/'flash_zb25vq32_read1.bin').read_bytes()
        h = gf.sensor_effect_hook(original)
        self.assertEqual((h['address'], h['replacement']), (BIAS+0x4a3f4, struct.pack('<I', 0xa44000ff).hex()))
        hooks = gf.filter_skip_hooks(original, BASE)
        self.assertEqual(len(hooks), 5)
        targets = {0xbfb0: BIAS+0xc02c, 0xc34c: BIAS+0xc2fc, 0xc3bc: BASE+gf.UP_EXIT_STUB,
                   0xc12c: BIAS+0xc1b4, 0xc3f4: BIAS+0xc478}
        for hook in hooks:
            site = hook['address']-BIAS
            word = struct.unpack('<I', bytes.fromhex(hook['replacement']))[0]
            self.assertEqual(word >> 26, 0, hex(site))
            off = word & 0x3ffffff; off -= (1 << 26) if off >> 25 else 0
            self.assertEqual(BIAS+site+4*off, targets[site], hex(site))
            self.assertEqual(original[site:site+4].hex(), hook['original'])

    def test_up_exit_stub(self):
        code = gf.up_exit_stub(BASE)
        blob = bytearray(0x10000); blob[gf.UP_EXIT_STUB:gf.UP_EXIT_STUB+len(code)] = code
        g = bytearray(0x100); g[97], g[99], g[101], g[102] = 3, 4, 5, 7
        called = []
        stub = bytearray(struct.pack('<I', 0x44004800))
        cpu = CPU([(BASE, blob, False), (0x020892c8, g, True), (BIAS+0xa814, stub, False)])
        cpu.r = [0x7000+i for i in range(32)]; cpu.r[0] = 0; cpu.r[2] = 0x020892c8; cpu.r[18] = 0x1234
        cpu.run(BASE+gf.UP_EXIT_STUB, stop=BIAS+0xc3cc, limit=100)
        self.assertEqual((g[97], g[99], g[101], g[102]), (0, 0, 0, 7))
        self.assertEqual((cpu.r[2], cpu.r[18], cpu.r[3]), (0x020892c8, 0x1234, 0x1234))


class OkButtonTests(unittest.TestCase):
    def run_event(self, event, subtype, mode, curve, selection=2):
        code = ir.trampoline(BASE, gf.CURVE_STATE, gf.CURVE_COUNT, menu_remap=True)
        blob = bytearray(0x10000); blob[ir.IDLE_CODE:ir.IDLE_CODE+len(code)] = code
        struct.pack_into('<I', blob, gf.CURVE_STATE, curve)
        glob = bytearray(0x200); struct.pack_into("<I", glob, ir.IDLE_COUNTER-0x02085e00, 41000)
        struct.pack_into('<I', glob, ir.MODE_WORD-0x02085e00, mode)
        stack = bytearray(0x100); struct.pack_into('<I', stack, 0x84, subtype)
        self.sel = bytearray(4); self.sel[0] = selection
        # menu activate handler stub: records (r3, r4, *r5, selection), clobbers caller-saved regs
        HANDLER, LOG = 0x02010000, 0x02011000
        from live_preview_patch import Code
        h = Code(); h.const(12, LOG); h.store(3, 12, 0); h.store(4, 12, 4); h.immediate(0x21, 13, 5, 0); h.store(13, 12, 8)
        h.const(13, ir.MENU_SELECTION); h.immediate(0x23, 13, 13, 0); h.store(13, 12, 12)
        h.immediate(0x21, 13, 12, 16); h.immediate(0x27, 13, 13, 1); h.store(13, 12, 16)
        for r in (3, 4, 5, 6, 7, 8, 11, 13, 15, 17): h.immediate(0x27, r, 0, 0x5a0+r)
        h.emit(0x44004800)
        self.log = bytearray(32)
        table = bytearray(8); struct.pack_into('<I', table, 4, HANDLER)
        cpu = CPU([(BASE, blob, True), (0x02085e00, glob, True), (0x021ffe00, stack, True), (ir.MENU_SELECTION, self.sel, True),
                   (HANDLER, bytearray(h.finish()), False), (LOG, self.log, True), (ir.MENU_ACTIVATE_SLOT-4, table, False)])
        cpu.r = [0x7000+i for i in range(32)]; cpu.r[0] = 0; cpu.r[1] = 0x021ffe80; cpu.r[2] = event
        before = cpu.r.copy()
        cpu.run(BASE+ir.IDLE_CODE, stop=BIAS+ir.DISPATCH+4, limit=400)
        return cpu, before, struct.unpack_from('<I', blob, gf.CURVE_STATE)[0], \
            struct.unpack_from('<I', glob, ir.IDLE_COUNTER-0x02085e00)[0]

    def test_ok_cycles_curves_in_camera_mode(self):
        for curve, nxt in ((0, 1), (1, 2), (3, 4), (4, 0), (9, 0)):     # AUTO -> FLAT -> ... -> AUTO
            cpu, before, got, idle = self.run_event(0x1c, 0, 3, curve)
            self.assertEqual((got, idle), (nxt, 0), curve)
            self.assertEqual(cpu.r[4], 0x02080000)
            keep = [r for r in range(32) if r not in (4, 9, 12, 13)]
            self.assertEqual([cpu.r[r] for r in keep], [before[r] for r in keep])

    def test_ok_ignored_outside_camera_or_on_release_and_other_keys(self):
        for event, subtype, mode in ((0x1c, 2, 3), (0x1c, 0, 6), (0x1c, 0, 8), (0x1e, 0, 3), (0x2e, 0, 3), (0x09, 0, 3)):
            cpu, before, got, idle = self.run_event(event, subtype, mode, 2)
            self.assertEqual(got, 2, (hex(event), subtype, mode))

    def test_menu_shutter_is_ok_and_fourth_button_goes_to_camera(self):
        # (event, subtype, selection) -> activate called?, selection seen by the handler
        for event, subtype, sel, calls, seen in ((0x29, 0, 2, 1, 2), (0x29, 2, 1, 0, None), (0x30, 0, 2, 1, 0),
                                                 (0x30, 2, 2, 0, None), (0x1c, 0, 1, 0, None), (0x1e, 0, 1, 0, None),
                                                 (0x2e, 0, 1, 0, None)):
            cpu, before, got, idle = self.run_event(event, subtype, 6, 3, sel)
            n = struct.unpack_from('<I', self.log, 16)[0]
            self.assertEqual(n, calls, (hex(event), subtype))
            self.assertEqual(cpu.r[2], event)                           # event dispatched unchanged
            self.assertEqual(cpu.r[11], before[11]); self.assertEqual(cpu.r[1], before[1])
            if calls:
                r3, r4, sub, s_ = struct.unpack_from('<4I', self.log, 0)
                self.assertEqual((r3, r4, sub, s_), (0, 1, 0, seen), hex(event))
            self.assertEqual(got, 3)
        for mode in (3, 8, 9):         # other modes: nothing extra
            for event in (0x29, 0x30):
                cpu, before, got, idle = self.run_event(event, 0, mode, 3, 2)
                self.assertEqual((cpu.r[2], self.sel[0], struct.unpack_from('<I', self.log, 16)[0]), (event, 2, 0), (mode, hex(event)))

if __name__ == '__main__': unittest.main()
