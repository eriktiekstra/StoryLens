"""Evidence-only metadata extraction; no inferred semantic interpretation."""

import base64
import hashlib
import json
import math
from pathlib import Path
import subprocess
import warnings

import PIL
from PIL import ExifTags, Image

ALGORITHM_VERSION = "metadata-1"


def json_value(value):
    if isinstance(value, bytes):
        return {"encoding": "base64", "data": base64.b64encode(value).decode("ascii")}
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if hasattr(value, "numerator") and hasattr(value, "denominator"):
        return {"numerator": value.numerator, "denominator": value.denominator}
    return str(value)


class MetadataExtractor:
    def __init__(self, executable="ffprobe", timeout=600):
        self.executable = executable
        self.timeout = timeout
        self._probe_version = None
        self.heif_version = None
        try:
            import pillow_heif
        except ImportError:
            pass
        else:
            pillow_heif.register_heif_opener()
            self.heif_version = pillow_heif.__version__

    def provenance(self, media_kind):
        if media_kind == "image":
            return {"algorithm": ALGORITHM_VERSION, "tool": "Pillow", "version": PIL.__version__,
                    "pillow_heif": self.heif_version}
        if self._probe_version is None:
            self._probe_version = self._run(["-version"]).splitlines()[0]
        return {"algorithm": ALGORITHM_VERSION, "tool": "ffprobe", "version": self._probe_version}

    def cache_key(self, media_kind):
        return hashlib.sha256(json.dumps(self.provenance(media_kind), sort_keys=True).encode()).hexdigest()

    def _run(self, arguments):
        try:
            result = subprocess.run([self.executable, *arguments], capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=self.timeout, check=False)
        except FileNotFoundError as error:
            raise ValueError("ffprobe is unavailable; install FFmpeg or configure ffprobe") from error
        except subprocess.TimeoutExpired as error:
            raise ValueError("ffprobe exceeded configured timeout") from error
        if result.returncode:
            raise ValueError("ffprobe failed: " + result.stderr.strip()[:2000])
        return result.stdout

    def extract(self, path: Path, media_kind: str):
        try:
            data = self._image(path) if media_kind == "image" else self._av(path)
        except Image.DecompressionBombError as error:
            raise ValueError("Image exceeds safe pixel limit") from error
        return {"schema_version": "1.0", "provenance": self.provenance(media_kind), **data}

    def _image(self, path):
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                exif = image.getexif()
                tags = {ExifTags.TAGS.get(tag, str(tag)): json_value(value) for tag, value in exif.items()}
                for tag, name in ((34665, "ExifIFD"), (34853, "GPSInfo")):
                    if tag in exif:
                        names = ExifTags.GPSTAGS if tag == 34853 else ExifTags.TAGS
                        tags[name] = {names.get(key, str(key)): json_value(value)
                                      for key, value in exif.get_ifd(tag).items()}
                result = {"format": image.format, "width": image.width, "height": image.height,
                          "mode": image.mode, "frame_count": getattr(image, "n_frames", 1),
                          "orientation": exif.get(274), "exif": tags,
                          "embedded": {key: json_value(value) for key, value in image.info.items()}}
                # No guessed timezone: keep capture strings in their original EXIF representation.
                if 34853 in exif:
                    result["gps"] = self._gps(exif.get_ifd(34853))
                # Some plugins consume the file while reading EXIF; verify a fresh handle.
                with Image.open(path) as validation:
                    validation.verify()
                return result

    @staticmethod
    def _gps(tags):
        def coordinate(value, reference, positive, negative, bound):
            if not isinstance(value, (tuple, list)) or len(value) != 3 or reference not in (positive, negative):
                return None
            try:
                degrees, minutes, seconds = map(float, value)
            except (ValueError, TypeError, ZeroDivisionError):
                return None
            if not all(math.isfinite(x) for x in (degrees, minutes, seconds)) or not (degrees >= 0 and 0 <= minutes < 60 and 0 <= seconds < 60):
                return None
            decimal = degrees + minutes / 60 + seconds / 3600
            if decimal > bound:
                return None
            return -decimal if reference == negative else decimal
        return {"latitude": coordinate(tags.get(2), tags.get(1), "N", "S", 90),
                "longitude": coordinate(tags.get(4), tags.get(3), "E", "W", 180)}

    def _av(self, path):
        raw = json.loads(self._run(["-v", "error", "-protocol_whitelist", "file,pipe",
                                   "-show_format", "-show_streams", "-of", "json", str(path)]))
        streams = raw.get("streams", [])
        if not streams:
            raise ValueError("ffprobe found no media streams")
        container = raw.get("format", {})
        # Keep rational timing and every stream/side-data tag, including VFR, rotation and HDR fields.
        return {"format": container.get("format_name"), "duration_seconds": container.get("duration"),
                "streams": streams, "tags": container.get("tags", {}), "raw": raw}
