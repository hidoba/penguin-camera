"""Release self-test (offline, no camera needed):  python3 -m unittest discover -s tools -p 'test_*.py'

1. The release image builds reproducibly from source (pinned SHA-256).
2. The installer's sector plan: stock -> release, release -> stock (uninstall),
   unknown flash refused, boot header protected, user data always preserved.
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import penguin_flash as pf

RELEASE_IMAGE_SHA256 = 'accf5a13376933b84697a0a4f3311386de4f326c023a714e00fa14e07bc35c70'   # version 1.0


def with_user(image, marker):
    out = bytearray(image); out[pf.USER.start:pf.USER.start+16] = marker*16; out[0x1d9000] = marker[0]
    return bytes(out)


class ReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import build_release, contextlib, io
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name)/'build'
        argv = sys.argv; sys.argv = ['build_release', '--version', 'test', '--output', str(out)]
        try:
            with contextlib.redirect_stdout(io.StringIO()): build_release.main()
        finally:
            sys.argv = argv
        cls.image = (out/'penguin-camera-test.bin').read_bytes()
        cls.stock = pf.original_image()

    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()

    def test_reproducible_image(self):
        self.assertEqual(pf.sha(self.image), RELEASE_IMAGE_SHA256)

    def test_install_plan_from_stock(self):
        current = with_user(self.stock, b'\x11')
        plan = pf.Plan(current, self.image, [('stock', self.stock)])
        self.assertEqual((plan.source, plan.order[0], plan.order[-1]), ('stock', pf.GUARDS, pf.ACTIVATION))
        self.assertEqual(plan.target[pf.USER], current[pf.USER])
        self.assertFalse(any(pf.USER.start <= a < pf.USER.stop for a in plan.order))
        self.assertIsNotNone(plan.payload)

    def test_uninstall_plan(self):
        current = with_user(self.image, b'\x22')
        plan = pf.Plan(current, self.stock, [('stock', self.stock), ('release', self.image)])
        self.assertEqual((plan.order[0], plan.order[-1]), (pf.ACTIVATION, pf.GUARDS))
        expected = bytearray(self.stock); expected[pf.USER] = current[pf.USER]
        self.assertEqual(plan.target, bytes(expected))

    def test_refusals(self):
        odd = bytearray(self.stock); odd[0x50000] ^= 1
        with self.assertRaises(ValueError): pf.Plan(bytes(odd), self.image, [('stock', self.stock)])
        bad = bytearray(self.image); bad[0x100] ^= 1
        with self.assertRaises(ValueError): pf.Plan(self.stock, bytes(bad), [('stock', self.stock)])


if __name__ == '__main__': unittest.main()
