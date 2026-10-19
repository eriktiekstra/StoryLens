"""Reproducible generated fixtures and measured cold-catalog/warm-catalog runs.

A cold catalog is NOT a cold OS/NAS cache. Reports are local/private operational
measurements, not accuracy evaluations or claims about other machines.
"""

import cProfile
from dataclasses import replace
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import math
from pathlib import Path
import platform
import pstats
import subprocess
import sys
import time
import wave

from . import __version__
from .ingest import discover, snapshot
from .service import Analyzer


def _integer(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in {minimum}..{maximum}")


def _new_directory(path, source=None):
    path = Path(path).expanduser().absolute()
    target = path.parent.resolve(strict=True) / path.name
    if source is not None and (target == source or target.is_relative_to(source)):
        raise ValueError("Benchmark workspace must be outside the source collection")
    if target.exists() or target.is_symlink():
        raise ValueError("Benchmark output already exists; choose a new directory")
    return target


def _hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def generate(output, *, images=1000, videos=2, audio=2, image_size=256,
             seconds=2, seed=0, ffmpeg="ffmpeg", timeout=30):
    """Write deterministic synthetic media only into a new directory."""
    from PIL import Image
    import numpy as np
    for value, name, maximum in ((images, "images", 100000), (videos, "videos", 1000), (audio, "audio", 1000)):
        _integer(value, name, 0, maximum)
    if images + videos + audio == 0:
        raise ValueError("At least one generated media file is required")
    _integer(image_size, "image_size", 16, 4096)
    _integer(seconds, "seconds", 1, 60)
    _integer(seed, "seed", 0, 2**32 - 1)
    root = _new_directory(output)
    root.mkdir(mode=0o700)
    rng = np.random.default_rng(seed)
    for index in range(images):
        pixels = rng.integers(0, 256, (image_size, image_size, 3), dtype=np.uint8)
        pixels[0, 0] = (index & 255, (index >> 8) & 255, (index >> 16) & 255)
        with Image.fromarray(pixels) as image:
            image.save(root / f"image-{index:06d}.png")
    for index in range(videos):
        path = root / f"video-{index:06d}.mp4"
        command = [ffmpeg, "-v", "error", "-nostdin", "-f", "lavfi", "-i",
                   f"testsrc2=size=160x90:rate=24:duration={seconds}", "-vf", f"hue=h={index * 35}",
                   "-an", "-c:v", "mpeg4", "-q:v", "4", "-pix_fmt", "yuv420p", str(path)]
        try:
            result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("Generated-video encoding timed out; incomplete fixture directory retained") from error
        if result.returncode:
            raise RuntimeError("Generated-video encoding failed: " + result.stderr.decode("utf-8", "replace")[-2000:])
    import array
    for index in range(audio):
        with wave.open(str(root / f"audio-{index:06d}.wav"), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(16000)
            frequency = 220 + index * 7
            for second in range(seconds):
                samples = array.array("h", (round(8000 * math.sin(2 * math.pi * frequency * (sample / 16000 + second)))
                                             for sample in range(16000)))
                if sys.byteorder != "little":
                    samples.byteswap()
                stream.writeframes(samples.tobytes())
    files = [{"path": path.name, "size": path.stat().st_size, "sha256": _hash(path)}
             for path in sorted(root.iterdir())]
    manifest = {"schema_version": "1.0", "kind": "storylens_benchmark_fixture", "root": str(root),
                "settings": {"images": images, "videos": videos, "audio": audio, "image_size": image_size,
                             "seconds": seconds, "seed": seed}, "environment": _environment(), "files": files,
                "caveats": ["Synthetic random images, test-pattern video and tones, not family-media accuracy evidence.",
                            "Byte reproducibility requires the same generator, codecs and dependency versions."]}
    (root / "fixture-manifest.json").write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return manifest


def _environment():
    libraries = {}
    for name in ("Pillow", "numpy", "mcp", "torch", "transformers", "faster-whisper"):
        try:
            libraries[name] = version(name)
        except PackageNotFoundError:
            libraries[name] = None
    return {"storylens": __version__, "python": platform.python_version(), "platform": platform.platform(),
            "machine": platform.machine(), "libraries": libraries}


def _peak_rss():
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                    "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                    "PagefileUsage", "PeakPagefileUsage")]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return None
        return counters.PeakWorkingSetSize
    try:
        import resource
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(value if sys.platform == "darwin" else value * 1024)
    except (ImportError, OSError):
        return None


def _profile_summary(profile, limit=20):
    stats = pstats.Stats(profile)
    rows = sorted(stats.stats.items(), key=lambda item: (-item[1][3], item[0]))[:limit]
    return [{"file": key[0], "line": key[1], "function": key[2], "primitive_calls": value[0],
             "calls": value[1], "self_seconds": value[2], "cumulative_seconds": value[3]}
            for key, value in rows]


def run(config, root, *, workspace, stage="all", passes=2, profile=False):
    """Measure model-free stage processing; never reuse or erase a user catalog."""
    _integer(passes, "passes", 2, 10)
    if stage not in {"metadata", "technical", "all"}:
        raise ValueError("Benchmark stage must be metadata, technical or all")
    if type(profile) is not bool:
        raise ValueError("profile must be boolean")
    source = Path(root).expanduser().resolve(strict=True)
    target = _new_directory(workspace, source)
    count, size = 0, 0
    for path in discover(source, config.exclusions):
        if isinstance(path, OSError):
            raise path
        info = snapshot(path)
        count += 1
        size += info.st_size
    if count == 0:
        raise ValueError("Benchmark source contains no recognized media")
    target.mkdir(mode=0o700)
    measured = replace(config, database=target / "catalog.sqlite3", embedding_model="", vision_model="",
                       audio_transcription_model="")
    report = {"schema_version": "1.0", "kind": "storylens_benchmark", "root": str(source),
              "workspace": str(target), "environment": _environment(), "stage": stage,
              "status": "running",
              "trust_stat": measured.trust_stat, "profile_enabled": profile,
              "source": {"recognized_references": count, "bytes": size}, "passes": [], "failed": 0,
              "caveats": ["Cold/warm refer to the new application catalog, not OS/NAS page caches.",
                          "Source inventory performs traversal/stat before measurement; no cache flush is attempted.",
                          "CPU time and peak RSS cover this Python process, not child FFmpeg/model processes.",
                          "Peak RSS is a process-lifetime high-water mark; it is not reset between passes.",
                          "Configured model inference is disabled in this benchmark; profile overhead affects timings.",
                          "Collection paths and error details are private; protect this report like a catalog."]}
    try:
        for index in range(passes):
            profiler = cProfile.Profile() if profile else None
            cpu, wall = time.process_time(), time.perf_counter()
            if profiler:
                profiler.enable()
            try:
                # A new service instance makes the application-stage cache, not
                # retained per-instance providers, the warm-pass variable.
                with Analyzer(measured) as app:
                    summary = app.scan(source, analyze=True, stage=stage)
            finally:
                if profiler:
                    profiler.disable()
            elapsed, cpu_elapsed = time.perf_counter() - wall, time.process_time() - cpu
            row = {"pass": index + 1, "catalog": "cold" if index == 0 else "warm", "wall_seconds": elapsed,
                   "process_cpu_seconds": cpu_elapsed, "peak_process_rss_bytes": _peak_rss(),
                   "files_per_second": summary["indexed"] / elapsed, "summary": summary}
            if profiler:
                profile_path = target / f"pass-{index + 1:02d}.pstats"
                profiler.dump_stats(str(profile_path))
                row.update(profile=str(profile_path), hotspots=_profile_summary(profiler))
            report["passes"].append(row)
            report["failed"] += summary["failed"]
        report["status"] = "failed" if report["failed"] else "succeeded"
    except (KeyboardInterrupt, SystemExit):
        report.update(status="interrupted", error="Benchmark interrupted; only completed passes are reported")
        raise
    except Exception as error:
        report["status"] = "failed"
        report["error"] = str(error)
        raise
    finally:
        (target / "benchmark.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return report
