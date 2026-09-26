"""Random-penguin folder (update 37): folder rules, roll orientation, pack format."""
import tempfile
import unittest
from pathlib import Path
from PIL import Image
import penguins
from prepare_print_penguins import validate


class Penguins(unittest.TestCase):
    def test_folder_rules(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            with self.assertRaises(ValueError): penguins.find(d)
            for name in ('10.png', '9.jpg', '.DS_Store', 'readme.md'): (d/name).write_bytes(b'x')
            self.assertEqual([p.name for p in penguins.find(d)], ['9.jpg', '10.png'])
            for i in range(63): (d/f'x{i}.png').write_bytes(b'x')
            with self.assertRaises(ValueError): penguins.find(d)

    def test_orientation_fit_and_pack(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            Image.new('RGB', (1200, 600), 'white').save(d/'1-landscape.png')      # turned: 384 x 768
            Image.new('RGB', (300, 3000), 'white').save(d/'2-tall.jpg')          # 102 x 1024 centred
            data, manifest = penguins.build(d)
            records = validate(data)
            self.assertEqual(len(records), 2)
            a, b = manifest['penguins']
            self.assertTrue(a['rotated_for_roll']); self.assertEqual(a['logical_roll_dimensions'], [384, 768])
            self.assertFalse(b['rotated_for_roll']); self.assertEqual(b['logical_roll_dimensions'], [384, 1024])
            for (off, n, w, h, _), r in zip(records, manifest['penguins']):
                self.assertEqual([w, h], r['encoded_dimensions']); self.assertLessEqual(n, penguins.MAX_JPEG)
                self.assertEqual(h, 384); self.assertEqual(w % 32, 0)

    def test_noisy_picture_quality_is_lowered_to_fit(self):
        import random
        rng = random.Random(1)
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            Image.frombytes('L', (384, 1024), bytes(rng.randrange(256) for _ in range(384*1024))).save(d/'noise.png')
            data, manifest = penguins.build(d)
            self.assertLess(manifest['penguins'][0]['quality'], penguins.QUALITY)
            self.assertLessEqual(manifest['penguins'][0]['bytes'], penguins.MAX_JPEG)


if __name__ == '__main__': unittest.main()
