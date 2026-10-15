"""Generated-media behavior tests; no private media or external services."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from storylens.duplicates import find_near_duplicates
from storylens.image_analysis import ImageTechnicalExtractor


def scene(size=(320, 240)):
    image = Image.new("RGB", size, (90, 130, 170))
    draw = ImageDraw.Draw(image)
    rng = np.random.default_rng(1984)
    for _ in range(30):
        x, y = rng.integers(0, size[0]), rng.integers(0, size[1])
        color = tuple(int(c) for c in rng.integers(20, 230, 3))
        draw.rectangle((int(x), int(y), int(x + 20), int(y + 17)), fill=color)
    draw.ellipse((40, 20, 190, 150), fill=(180, 140, 70))
    draw.line((0, 210, 300, 30), fill="white", width=4)
    return image


class ImageTechnicalTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.extractor = ImageTechnicalExtractor()

    def extract(self, image, name="image.png", **options):
        path = self.base / name
        image.save(path, **options)
        return self.extractor.extract(path)

    def test_sharpness_blur_and_normalized_contract(self):
        original = scene()
        sharp = self.extract(original)
        blurred = self.extract(original.filter(ImageFilter.GaussianBlur(3)), "blurred.png")
        self.assertGreater(sharp["metrics"]["sharpness"], blurred["metrics"]["sharpness"] * 3)
        self.assertLess(sharp["metrics"]["blur_proxy"], blurred["metrics"]["blur_proxy"])
        self.assertEqual(sharp["schema_version"], "1.0")
        self.assertEqual(len(sharp["similarity"]["perceptual_hash"]), 16)
        self.assertEqual(len(sharp["similarity"]["color_histogram"]), 64)
        self.assertAlmostEqual(sum(sharp["similarity"]["color_histogram"]), 1)
        for key in ("sharpness", "blur_proxy", "contrast", "noise_estimate"):
            self.assertTrue(0 <= sharp["metrics"][key] <= 1)
        json.dumps(sharp, allow_nan=False)


    def test_exposure_clipping_and_contrast(self):
        black = self.extract(Image.new("RGB", (64, 64), "black"), "black.png")
        white = self.extract(Image.new("RGB", (64, 64), "white"), "white.png")
        middle = self.extract(Image.new("RGB", (64, 64), (128, 128, 128)), "gray.png")
        self.assertEqual(black["metrics"]["exposure"]["clipped_shadows_fraction"], 1)
        self.assertEqual(white["metrics"]["exposure"]["clipped_highlights_fraction"], 1)
        self.assertEqual(middle["metrics"]["exposure"]["clipped_shadows_fraction"], 0)
        self.assertEqual(middle["metrics"]["exposure"]["clipped_highlights_fraction"], 0)
        self.assertAlmostEqual(middle["metrics"]["exposure"]["mean_luminance"], 128 / 255, places=6)
        self.assertLess(black["metrics"]["contrast"], 1e-5)
        half = Image.new("RGB", (64, 64), "black")
        ImageDraw.Draw(half).rectangle((32, 0, 63, 63), fill="white")
        mixed = self.extract(half, "mixed.png")
        self.assertEqual(mixed["metrics"]["exposure"]["clipped_shadows_fraction"], 0.5)
        self.assertEqual(mixed["metrics"]["exposure"]["clipped_highlights_fraction"], 0.5)
        self.assertAlmostEqual(mixed["metrics"]["contrast"], 1, places=6)

    def test_noise_estimate_increases_with_generated_noise(self):
        clean = np.full((256, 256, 3), 128, dtype=np.uint8)
        noise = np.random.default_rng(17).normal(0, 12, (256, 256, 1))
        noisy = np.clip(clean + noise, 0, 255).astype(np.uint8)
        clean_result = self.extract(Image.fromarray(clean), "clean.png")
        noisy_result = self.extract(Image.fromarray(noisy), "noise.png")
        self.assertEqual(clean_result["metrics"]["noise_estimate"], 0)
        self.assertGreater(noisy_result["metrics"]["noise_estimate"], 0.02)


    def test_exif_orientation_matches_physically_rotated_image(self):
        original = scene()
        exif = Image.Exif()
        exif[274] = 6
        tagged = self.extract(original, "tagged.png", exif=exif)
        rotated = self.extract(original.transpose(Image.Transpose.ROTATE_270), "rotated.png")
        self.assertEqual(tagged["resolution"], {"width": 320, "height": 240,
                         "megapixels": 0.0768, "display_width": 240, "display_height": 320})
        self.assertTrue(tagged["preprocessing"]["orientation_applied"])
        self.assertEqual(tagged["similarity"], rotated["similarity"])
        self.assertEqual(tagged["metrics"], rotated["metrics"])

    def test_bounded_derivative_read_only_and_alpha_background(self):
        path = self.base / "large.jpg"
        scene((2048, 1200)).save(path)
        before = path.read_bytes()
        result = self.extractor.extract(path)
        self.assertEqual(result["resolution"]["width"], 2048)
        self.assertEqual(result["preprocessing"]["derivative_width"], 1024)
        self.assertLessEqual(result["preprocessing"]["derivative_height"], 1024)
        self.assertEqual(path.read_bytes(), before)
        transparent = self.extract(Image.new("RGBA", (32, 32), (0, 0, 0, 0)), "alpha.png")
        self.assertEqual(transparent["metrics"]["exposure"]["clipped_highlights_fraction"], 1)

    def test_pixel_safety_and_corruption_raise(self):
        path = self.base / "safety.png"
        Image.new("RGB", (20, 20)).save(path)
        with patch("storylens.image_analysis.MAX_PIXELS", 100):
            with self.assertRaisesRegex(ValueError, "safe pixel limit"):
                self.extractor.extract(path)
        with patch("PIL.Image.MAX_IMAGE_PIXELS", 250):
            with self.assertRaisesRegex(ValueError, "safe pixel limit"):
                self.extractor.extract(path)
        path.write_bytes(b"broken image")
        with self.assertRaises(OSError):
            self.extractor.extract(path)

    def test_tiny_images_and_cache_provenance(self):
        result = self.extract(Image.new("L", (1, 1), 128))
        self.assertEqual(result["metrics"]["noise_estimate"], 0)
        json.dumps(result, allow_nan=False)
        key = self.extractor.cache_key()
        with patch("storylens.image_analysis.ALGORITHM_VERSION", "image-technical-next"):
            self.assertNotEqual(key, self.extractor.cache_key())

    def test_generated_near_duplicates_and_unrelated_image(self):
        original = scene()
        first = self.extract(original, "original.png")
        second = self.extract(original, "compressed.jpg", quality=90)
        unrelated = self.extract(Image.fromarray(np.random.default_rng(19).integers(
            0, 256, (240, 320, 3), dtype=np.uint8)), "unrelated.png")
        result = find_near_duplicates([{"id": "a", "result": first},
                                      {"id": "b", "result": second},
                                      {"id": "c", "result": unrelated}])
        self.assertEqual([(x["left_id"], x["right_id"]) for x in result["items"]], [("a", "b")])
        self.assertLessEqual(result["items"][0]["hamming_distance"], 6)

    def test_color_aspect_and_low_information_reject_hash_collisions(self):
        original = self.extract(scene())
        color = deepcopy(original)
        color["similarity"]["color_histogram"] = [1.0] + [0.0] * 63
        aspect = deepcopy(original)
        aspect["similarity"]["aspect_ratio"] *= 2
        flat = self.extract(Image.new("RGB", (320, 240), "red"), "flat.png")
        flat["similarity"]["perceptual_hash"] = original["similarity"]["perceptual_hash"]
        records = [{"id": key, "result": result} for key, result in
                   (("a", original), ("b", color), ("c", aspect), ("d", flat))]
        self.assertEqual(find_near_duplicates(records)["items"], [])
        black = self.extract(Image.new("RGB", (64, 64), "black"), "black.png")
        white = self.extract(Image.new("RGB", (64, 64), "white"), "white.png")
        self.assertEqual(find_near_duplicates([{"id": "a", "result": black},
                                              {"id": "b", "result": white}])["items"], [])

    def test_deterministic_pagination_and_fixed_candidate_bound(self):
        result = self.extract(scene())
        records = [{"id": f"{i:03}", "result": result} for i in range(10)]
        expected = [(f"{i:03}", f"{j:03}") for i in range(10) for j in range(i + 1, min(i + 4, 10))]
        with patch("storylens.duplicates.MAX_CANDIDATES", 3):
            cursor = ""
            actual = []
            for _ in range(20):
                page = find_near_duplicates(reversed(records), limit=2, after=cursor)
                actual.extend((item["left_id"], item["right_id"]) for item in page["items"])
                cursor = page["next_cursor"]
                if cursor is None:
                    break
            else:
                self.fail("Pagination did not terminate")
            self.assertEqual(actual, expected)

    def test_large_equal_hash_group_has_bounded_page(self):
        result = self.extract(scene())
        records = ({"id": f"{i:05}", "result": result} for i in range(20_000))
        first = find_near_duplicates(records, limit=5)
        self.assertEqual(len(first["items"]), 5)
        self.assertEqual(first["items"][-1]["right_id"], "00005")
        self.assertIsNotNone(first["next_cursor"])

    def test_hamming_radius_tree_and_validation(self):
        result = self.extract(scene())
        records = []
        for index, value in enumerate((0, 1, 3, 7, 15, (1 << 63))):
            copy = deepcopy(result)
            copy["similarity"]["perceptual_hash"] = f"{value:016x}"
            records.append({"id": str(index), "result": copy})
        page = find_near_duplicates(records, max_distance=1)
        expected = [(str(i), str(j)) for i in range(6) for j in range(i + 1, 6)
                    if ((0, 1, 3, 7, 15, 1 << 63)[i] ^ (0, 1, 3, 7, 15, 1 << 63)[j]).bit_count() <= 1]
        self.assertEqual([(x["left_id"], x["right_id"]) for x in page["items"]], expected)
        for kwargs in ({"limit": 0}, {"limit": 501}, {"max_distance": -1},
                       {"max_distance": 65}, {"after": "garbage"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                find_near_duplicates(records, **kwargs)
        cursor = find_near_duplicates(records, limit=1)["next_cursor"]
        with self.assertRaises(ValueError):
            find_near_duplicates(records, max_distance=2, after=cursor)
        self.assertEqual(find_near_duplicates([])["items"], [])
        with self.assertRaises(ValueError):
            find_near_duplicates(records + records[:1])

    def test_exact_hash_survives_tree_visit_cap(self):
        result = self.extract(scene())
        zero = deepcopy(result)
        zero["similarity"]["perceptual_hash"] = "0000000000000000"
        records = [{"id": "a", "result": zero}, {"id": "b", "result": result},
                   {"id": "c", "result": result}]
        with patch("storylens.duplicates.MAX_TREE_VISITS", 1):
            pairs = find_near_duplicates(records, max_distance=0)["items"]
        self.assertEqual([(x["left_id"], x["right_id"]) for x in pairs], [("b", "c")])


if __name__ == "__main__":
    unittest.main()
