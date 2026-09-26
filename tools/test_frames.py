"""Frames folder (update 36): slot choice, hooks, and the stock up/down handlers
run in the emulator with every RAM hook applied."""
import struct
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from or1k_subset import CPU
from ui_trace_patch import BIAS
import frames
import gray_filters as gf

ROOT = Path(__file__).resolve().parents[1]
BASE = 0x02147240
G = 0x020892c8                   # camera state: +97 category (3 = frame), +102 frame index
DOWN, UP = 0xbef4, 0xc1d4        # stock down / up key handlers (flash offsets)
STUBS = (0xf384, 0x45344, 0x2144c, 0xa3b4, 0xf454, 0x4a3e4, 0x40f10, 0x480e8, 0x40b74,
         0x40e6c, 0xa814, 0x629c, 0xa6e0, 0xbeac)
LOAD_FRAME = 0x40b74             # r3 = frame resource ID


class Recorder(CPU):
    def access(self, address, size, value=None):
        if value is None and size == 4 and address-BIAS in STUBS:
            self.calls.append((address-BIAS, self.r[3]))
        return CPU.access(self, address, size, value)


class Frames(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = (ROOT/'flash_zb25vq32_read1.bin').read_bytes()

    def machine(self, slots):
        app = bytearray(self.original[0x2400:0x83e00])
        stub = bytearray(0x1000); code = gf.up_exit_stub(BASE); o = gf.UP_EXIT_STUB & 0xfff; stub[o:o+len(code)] = code
        hooks = gf.filter_skip_hooks(self.original, BASE) + frames.hooks(self.original, slots)
        for h in hooks:
            off = h['address']-0x02000000
            self.assertEqual(app[off:off+4].hex(), h['original'])
            app[off:off+4] = bytes.fromhex(h['replacement'])
        for target in STUBS:                                   # l.addi r11,r0,0 ; l.jr r9
            app[target-0x2400:target-0x2400+8] = struct.pack('<II', 0x9d600000, 0x44004800)
        state = bytearray(0x100); glob = bytearray(0x100)
        return app, stub, state, glob

    def press(self, m, handler):
        app, stub, state, glob = m
        event = bytearray(struct.pack('<I', 2))
        cpu = Recorder([(0x02000000, app, False), (BASE+(gf.UP_EXIT_STUB & ~0xfff), stub, False),
                        (G, state, True), (0x02085e00, glob, True), (0x021ff000, bytearray(0x1000), True),
                        (0x021fe000, event, False)])
        cpu.calls = []; cpu.r[1] = 0x021fff00; cpu.r[3] = 0x1234; cpu.r[4] = 1; cpu.r[5] = 0x021fe000
        cpu.run(BIAS+handler, limit=10000)
        loads = [r3 for target, r3 in cpu.calls if target == LOAD_FRAME]
        return state[97], state[102], loads

    def cycle(self, slots, handler, presses):
        m = self.machine(slots); seen = []
        for _ in range(presses):
            category, index, loads = self.press(m, handler)
            seen.append(None if category != 3 else (index, loads[-1] if loads else None))
            self.assertIn(category, (0, 3))
        return seen

    def test_down_and_up_offer_exactly_n_frames(self):
        for n in (1, 2, 4, 16, 17):
            slots = frames.assign(self.original, n)
            down = self.cycle(slots, DOWN, 2*(n+1))
            want = [(k, slots[k]) for k in range(n)] + [None]
            self.assertEqual(down, want*2, n)
            up = self.cycle(slots, UP, 2*(n+1))
            self.assertEqual(up, want[::-1][1:]+[None]+want[::-1][1:]+[None], n)

    def test_slots_are_the_largest_and_hooks_minimal(self):
        sizes = frames.slot_sizes(self.original); stock = frames.stock_table(self.original)
        for n in range(1, 18):
            slots = frames.assign(self.original, n)
            self.assertEqual(len(set(slots)), n)
            self.assertEqual(sorted(sizes[s] for s in slots), sorted(sizes.values())[-n:])
            hooks = frames.hooks(self.original, slots)
            moved = sum(stock[k] != s for k, s in enumerate(slots))
            self.assertEqual(len(hooks), moved+(5 if n < 17 else 0))
        self.assertEqual(frames.hooks(self.original, frames.assign(self.original, 17)), [])

    def test_folder_rules(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            with self.assertRaises(ValueError): frames.find(d)
            for name in ('10-b.png', '2-a.jpg', '.hidden.png', 'notes.txt', '1.PNG'):
                (d/name).write_bytes(b'x')
            self.assertEqual([p.name for p in frames.find(d)], ['1.PNG', '2-a.jpg', '10-b.png'])
            for i in range(20): (d/f'x{i}.png').write_bytes(b'x')
            with self.assertRaises(ValueError): frames.find(d)

    def test_alpha_and_scaling(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'f.png'
            im = Image.new('RGBA', (1280, 720), (255, 255, 255, 255))
            im.paste((0, 0, 0, 0), (0, 0, 640, 720)); im.save(p)
            rgb, mask = frames.load(p)
            self.assertEqual(rgb.size, frames.SIZE)
            self.assertTrue(mask[10] and not mask[630])
            Image.new('RGB', (640, 480)).save(p)
            with self.assertRaises(ValueError): frames.load(p)
            Image.new('RGB', (640, 360)).save(p)
            self.assertIsNone(frames.load(p)[1])


if __name__ == '__main__': unittest.main()
