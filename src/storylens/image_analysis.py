"""Bounded, local image measurements; none of these proxies measure relevance."""

import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import warnings

import numpy as np
import PIL
from PIL import Image, ImageOps

ALGORITHM_VERSION = "image-technical-1"
MAX_DIMENSION = 1024
MAX_PIXELS = 50_000_000


class ImageTechnicalExtractor:
    def __init__(self):
        # Only the eight low-frequency DCT rows are needed for a 64-bit pHash.
        positions = np.arange(32, dtype=np.float64) + 0.5
        self._dct = np.cos(np.pi / 32 * np.arange(8)[:, None] * positions)
        self._dct[0] *= 1 / np.sqrt(2)
        self._dct *= np.sqrt(2 / 32)

    def provenance(self) -> dict:
        try:
            heif_version = version("pillow-heif")
        except PackageNotFoundError:
            heif_version = None
        return {
            "algorithm": ALGORITHM_VERSION,
            "dependencies": {"Pillow": PIL.__version__, "numpy": np.__version__,
                             "pillow_heif": heif_version},
            "parameters": {"max_dimension": MAX_DIMENSION, "max_pixels": MAX_PIXELS,
                           "resampling": "LANCZOS", "frame_index": 0,
                           "orientation": "EXIF transpose", "alpha_background": "white",
                           "luminance": "Rec.601 coefficients on encoded RGB",
                           "clipping_thresholds": [5 / 255, 250 / 255],
                           "sharpness_variance_scale": 0.01,
                           "hash": "DCT-32-low8-median-AC-DC-zero",
                           "histogram_bins_per_channel": 4},
        }

    def cache_key(self) -> str:
        return hashlib.sha256(json.dumps(self.provenance(), sort_keys=True).encode()).hexdigest()

    def extract(self, path: Path) -> dict:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(path) as image:
                    width, height = image.size
                    if width * height > MAX_PIXELS:
                        raise ValueError("Image exceeds safe pixel limit")
                    orientation = image.getexif().get(274)
                    swapped = orientation in (5, 6, 7, 8)
                    display_width, display_height = (height, width) if swapped else (width, height)
                    source_mode = image.mode
                    # JPEG draft decoding saves work; all later analysis uses <=1024px.
                    image.draft("RGB", (MAX_DIMENSION, MAX_DIMENSION))
                    image.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.Resampling.LANCZOS)
                    derivative = ImageOps.exif_transpose(image)
                    if "A" in derivative.getbands() or "transparency" in derivative.info:
                        rgba = derivative.convert("RGBA")
                        background = Image.new("RGBA", rgba.size, "white")
                        derivative = Image.alpha_composite(background, rgba).convert("RGB")
                    else:
                        derivative = derivative.convert("RGB")
                    return self._measure(derivative, width, height, display_width, display_height,
                                         orientation, source_mode)
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
            raise ValueError("Image exceeds safe pixel limit") from error

    def _measure(self, image, width, height, display_width, display_height, orientation, source_mode):
        rgb = np.asarray(image, dtype=np.float32) / 255
        luminance = rgb @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
        stddev = float(luminance.std())
        if min(luminance.shape) >= 3:
            center = luminance[1:-1, 1:-1]
            laplacian = (luminance[:-2, 1:-1] + luminance[2:, 1:-1]
                         + luminance[1:-1, :-2] + luminance[1:-1, 2:] - 4 * center)
            variance = float(laplacian.var())
            gradient = (np.abs(luminance[1:-1, 2:] - luminance[1:-1, :-2])
                        + np.abs(luminance[2:, 1:-1] - luminance[:-2, 1:-1]))
            # Prefer the smoothest quarter; texture can still contaminate this estimate.
            smooth = laplacian[gradient <= np.quantile(gradient, 0.25)]
            noise = float(np.median(np.abs(smooth - np.median(smooth))) / (0.67448975 * np.sqrt(20)))
        else:
            variance, noise = 0.0, 0.0
        sharpness = variance / (variance + 0.01)
        small = np.asarray(image.convert("L").resize((32, 32), Image.Resampling.LANCZOS), dtype=np.float64)
        coefficients = (self._dct @ small @ self._dct.T).ravel()
        bits = coefficients > np.median(coefficients[1:])
        bits[0] = False
        perceptual_hash = int.from_bytes(np.packbits(bits).tobytes(), "big")
        bins = np.minimum((rgb * 4).astype(np.uint8), 3)
        indices = bins[..., 0] * 16 + bins[..., 1] * 4 + bins[..., 2]
        histogram = np.bincount(indices.ravel(), minlength=64).astype(np.float64)
        histogram /= histogram.sum()
        return {
            "schema_version": "1.0", "provenance": self.provenance(),
            "resolution": {"width": width, "height": height, "megapixels": width * height / 1_000_000,
                           "display_width": display_width, "display_height": display_height},
            "preprocessing": {"exif_orientation": orientation,
                              "orientation_applied": orientation in (2, 3, 4, 5, 6, 7, 8),
                              "derivative_width": image.width, "derivative_height": image.height,
                              "max_dimension": MAX_DIMENSION, "source_mode": source_mode,
                              "color_space": "RGB", "alpha_background": "white", "frame_index": 0},
            "metrics": {"sharpness": sharpness, "blur_proxy": 1 - sharpness,
                        "exposure": {"mean_luminance": float(luminance.mean()),
                                     "clipped_shadows_fraction": float(np.mean(luminance <= 5 / 255)),
                                     "clipped_highlights_fraction": float(np.mean(luminance >= 250 / 255))},
                        "contrast": min(1.0, 2 * stddev), "noise_estimate": min(1.0, noise)},
            "similarity": {"perceptual_hash": f"{perceptual_hash:016x}", "hash_bits": 64,
                           "color_histogram": histogram.tolist(),
                           "aspect_ratio": display_width / display_height,
                           "luminance_stddev": stddev},
            "caveats": [
                "Technical proxies, not semantic relevance or aesthetic quality scores.",
                "Sharpness, contrast and noise depend on scene detail and derivative scale; blur is not a focus diagnosis.",
                "Noise is a smooth-region Laplacian MAD estimate; texture, compression and resizing can bias it; tiny images return zero.",
                "Exposure measures encoded Rec.601 luminance, not sensor exposure or HDR radiometry; clipping thresholds are 5/255 and 250/255.",
                "Only the first frame is analyzed; EXIF orientation is applied; transparency is composited on white; ICC profiles are not color-managed.",
                "Perceptual hash and coarse color histogram identify conservative duplicate candidates, not proof of identity.",
            ],
        }
