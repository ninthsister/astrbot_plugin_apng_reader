import base64
import io
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from decoder import decode_apng, DecodeLimit


class DecoderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'image.dat'

    def tearDown(self):
        self.tmp.cleanup()

    def frame(self, color):
        return Image.new('RGBA', (16, 16), color)

    def pixel(self, url, xy=(0, 0)):
        return Image.open(io.BytesIO(base64.b64decode(url.split(',', 1)[1]))).getpixel(xy)

    def test_static_png_unchanged(self):
        self.frame('red').save(self.path, format='PNG')
        self.assertIsNone(decode_apng(self.path))

    def test_separate_poster_and_hidden_frame(self):
        self.frame('red').save(self.path, format='PNG', save_all=True,
                              append_images=[self.frame('blue')], default_image=True)
        result = decode_apng(self.path)
        self.assertTrue(result['poster_skipped'])
        self.assertEqual(result['total'], 1)
        self.assertEqual(self.pixel(result['frames'][0]), (0, 0, 255, 255))

    def test_regular_apng_and_last(self):
        self.frame('red').save(self.path, format='PNG', save_all=True,
                              append_images=[self.frame('blue')], duration=[1, 100])
        result = decode_apng(self.path)
        self.assertEqual(result['indices'], [1, 2])
        self.assertEqual(self.pixel(decode_apng(self.path, mode='last')['frames'][0]), (0, 0, 255, 255))

    def test_samples_include_last(self):
        frames = [self.frame((i * 20, 0, 0, 255)) for i in range(10)]
        frames[0].save(self.path, format='PNG', save_all=True, append_images=frames[1:])
        result = decode_apng(self.path, max_frames=3)
        self.assertEqual(result['indices'], [1, 5, 10])
        self.assertTrue(result['sampled'])
        with self.assertRaises(DecodeLimit):
            decode_apng(self.path, max_decode_frames=2)

    def test_blend_over_composition(self):
        overlay = self.frame((0, 0, 0, 0))
        overlay.putpixel((0, 0), (0, 0, 255, 255))
        self.frame('red').save(self.path, format='PNG', save_all=True,
                              append_images=[overlay], blend=[0, 1], disposal=[0, 0])
        url = decode_apng(self.path, mode='last')['frames'][0]
        self.assertEqual(self.pixel(url), (0, 0, 255, 255))
        self.assertEqual(self.pixel(url, (1, 1)), (255, 0, 0, 255))

    def test_corrupt_png_ignored(self):
        self.path.write_bytes(b'\x89PNG\r\n\x1a\n' + b'broken')
        self.assertIsNone(decode_apng(self.path))


if __name__ == '__main__':
    unittest.main()
