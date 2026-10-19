"""Exercise a core-only installed distribution with generated, local media.

Run with ``python -I scripts/installed_smoke.py --checkout /path/to/checkout``
from a directory outside the checkout, after installing its wheel without extras.
"""

import argparse
import hashlib
import importlib.metadata
import importlib.resources
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import tempfile
import wave

from PIL import Image
import storylens


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def run(command):
    environment = dict(os.environ)
    for name in ("PYTHONPATH", "PYTHONHOME"):
        environment.pop(name, None)
    completed = subprocess.run(command, check=True, capture_output=True,
                               text=True, encoding="utf-8", timeout=90,
                               env=environment)
    return completed.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", required=True, type=Path)
    checkout = parser.parse_args().checkout.resolve()
    require(not Path.cwd().resolve().is_relative_to(checkout),
            "Installed smoke must run outside the checkout")
    require(not Path(storylens.__file__).resolve().is_relative_to(checkout),
            "Imported StoryLens from the checkout instead of the installed wheel")
    require(storylens.__version__ == importlib.metadata.version("storylens"),
            "Package and distribution versions disagree")
    for optional in ("mcp", "pillow_heif", "torch", "transformers", "sentencepiece",
                     "safetensors", "faster_whisper", "pyannote"):
        require(importlib.util.find_spec(optional) is None,
                f"Core-only environment unexpectedly contains {optional}")
    schemas = importlib.resources.files("storylens").joinpath("schemas")
    for name in ("results-v1.schema.json", "curation-v1.schema.json", "storyboard-v1.schema.json"):
        json.loads(schemas.joinpath(name).read_text(encoding="utf-8"))

    entrypoint = Path(sysconfig.get_path("scripts")) / ("storylens.exe" if os.name == "nt" else "storylens")
    require(entrypoint.is_file(), "Installed storylens console entry point is missing")
    require(run([str(entrypoint), "--version"]).strip() == storylens.__version__,
            "Installed console version disagrees with distribution")
    with tempfile.TemporaryDirectory(prefix="storylens-installed-") as temporary:
        root = Path(temporary)
        media = root / "generated media"
        media.mkdir()
        Image.new("RGB", (96, 64), "blue").save(media / "photo-映像.png")
        run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-f", "lavfi",
             "-i", "color=c=blue:s=80x48:r=10:d=1", "-c:v", "mpeg4", str(media / "clip.mp4")])
        with wave.open(str(media / "silence.wav"), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(b"\x00\x00" * 16000)
        originals = {source: hashlib.sha256(source.read_bytes()).hexdigest() for source in media.iterdir()}
        arguments = ["--database", str(root / "catalog.sqlite3"), "--json"]
        scanned = json.loads(run([str(entrypoint), *arguments, "scan", str(media)]))
        require(scanned["indexed"] == 3 and scanned["failed"] == 0, scanned)
        command = [sys.executable, "-I", "-m", "storylens", *arguments]

        def cli(*args):
            return json.loads(run([*command, *args]))

        analyzed = cli("analyze", str(media))
        require(analyzed["failed"] == 0, analyzed)
        for stage, count in (("metadata", 3), ("image_technical", 1),
                             ("video_technical", 1), ("audio_technical", 1)):
            require(analyzed["stages"][stage]["analyzed"] == count, analyzed)
        page = cli("list")
        require(len(page["items"]) == 3 and page["next_cursor"] is None, page)
        require({item["kind"] for item in page["items"]} == {"image", "video", "audio"}, page)
        for item in page["items"]:
            details = cli("show", item["id"])["media"]
            require(set(details["analysis"]) == {"metadata", f'{item["kind"]}_technical'}, details)
            require(not details["failures"], details)
            if item["kind"] == "video":
                scenes = cli("scenes", item["id"])
                require(len(scenes["items"]) == 1, scenes)
                with Image.open(scenes["items"][0]["representative_frame"]["path"]) as frame:
                    require(frame.format == "JPEG", "Expected a real generated JPEG artifact")
                    frame.verify()
        cached = cli("analyze", str(media))
        require(cached["failed"] == 0 and cached["cache_hits"] == 3, cached)
        for source, digest in originals.items():
            require(hashlib.sha256(source.read_bytes()).hexdigest() == digest,
                    f"Analysis modified source {source}")
    print(f"Installed core-only StoryLens {storylens.__version__}: generated image/video/audio CLI smoke passed")


if __name__ == "__main__":
    main()
