"""Local FFmpeg evidence extraction; proxies are not relevance or quality judgments."""

from array import array
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading

import PIL
from PIL import Image, ImageChops, ImageStat


ALGORITHM_VERSION = "video-technical-1"
MAX_SAMPLES = 64


def _number(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _rational(value):
    return {"numerator": value.numerator, "denominator": value.denominator}


class VideoTechnicalExtractor:
    def __init__(self, ffmpeg="ffmpeg", ffprobe="ffprobe", timeout=600,
                 scene_threshold=0.3, minimum_scene_seconds=0.5, *, max_scenes=256):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        if not math.isfinite(scene_threshold) or not 0 < scene_threshold <= 1:
            raise ValueError("scene_threshold must be in (0, 1]")
        if not math.isfinite(minimum_scene_seconds) or minimum_scene_seconds < 0:
            raise ValueError("minimum_scene_seconds must be finite and nonnegative")
        if isinstance(max_scenes, bool) or not isinstance(max_scenes, int) or max_scenes < 1:
            raise ValueError("max_scenes must be a positive integer")
        self.ffmpeg, self.ffprobe, self.timeout = ffmpeg, ffprobe, timeout
        self.scene_threshold = scene_threshold
        self.minimum_scene_seconds = minimum_scene_seconds
        self.max_scenes = max_scenes
        self._versions = None

    def _run(self, executable, arguments, *, consume=None, max_output=2 * 1024 * 1024):
        """Drain both pipes concurrently, retaining only bounded output/error payloads."""
        try:
            process = subprocess.Popen([str(executable), *arguments], stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except FileNotFoundError as error:
            raise ValueError(f"{executable} unavailable; install FFmpeg or configure its path") from error
        output, errors, failures = bytearray(), bytearray(), []

        def drain(pipe, target, limit, callback=None):
            try:
                while chunk := pipe.read(65536):
                    if callback is not None:
                        callback(chunk)
                    else:
                        if len(target) + len(chunk) > limit:
                            raise ValueError("FFmpeg output exceeds bounded payload limit")
                        target.extend(chunk)
            except Exception as error:
                failures.append(error)
                process.kill()
            finally:
                pipe.close()

        threads = [threading.Thread(target=drain, args=(process.stdout, output, max_output, consume)),
                   threading.Thread(target=drain, args=(process.stderr, errors, 256 * 1024))]
        for thread in threads:
            thread.start()
        timed_out = False
        try:
            process.wait(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            process.wait()
        except BaseException:
            process.kill()
            process.wait()
            raise
        finally:
            for thread in threads:
                thread.join()
        if timed_out:
            raise ValueError("FFmpeg exceeded configured timeout")
        if failures:
            raise ValueError(str(failures[0])) from failures[0]
        if process.returncode:
            raise ValueError("FFmpeg failed: " + errors.decode("utf-8", "replace")[-2000:])
        return bytes(output), errors.decode("utf-8", "replace")

    def provenance(self):
        if self._versions is None:
            self._versions = {
                "ffmpeg": self._run(self.ffmpeg, ["-version"])[0].decode().splitlines()[0],
                "ffprobe": self._run(self.ffprobe, ["-version"])[0].decode().splitlines()[0],
            }
        return {"algorithm": ALGORITHM_VERSION, "tools": dict(self._versions),
                "scene_threshold": self.scene_threshold,
                "minimum_scene_seconds": self.minimum_scene_seconds,
                "max_scenes": self.max_scenes, "max_frame_samples": MAX_SAMPLES,
                "pillow_version": PIL.__version__,
                "resource_limits": {"decoded_video_pixels": 16_777_216, "audio_channels": 32,
                    "stderr_bytes_per_process": 262144, "probe_payload_bytes": 2097152,
                    "jpeg_bytes_per_scene": 2097152, "metadata_line_bytes": 16384},
                "scene_method": "FFmpeg select scene score on decoded source frames",
                "representative_method": "first decoded frame at/after scene midpoint, scene-start fallback for sparse PTS; final target clamped to last PTS",
                "timing_method": "decoded best_effort_timestamp and stream time_base; never frame/fps",
                "derivative_method": "FFmpeg autorotation, bounded scale, 8-bit JPEG; no HDR tone mapping"}

    def cache_key(self):
        return hashlib.sha256(json.dumps(self.provenance(), sort_keys=True).encode()).hexdigest()

    @staticmethod
    def _lines(callback):
        pending = bytearray()

        def consume(chunk):
            pending.extend(chunk)
            while (end := pending.find(b"\n")) >= 0:
                line = bytes(pending[:end]).decode("utf-8", "replace")
                del pending[:end + 1]
                callback(line)
            if len(pending) > 16384:
                raise ValueError("FFmpeg metadata line exceeds limit")
        return consume, pending

    def _input(self, path, *, verbose="error"):
        return ["-hide_banner", "-nostdin", "-v", verbose, "-xerror", "-copyts",
                "-protocol_whitelist", "file,pipe", "-i", str(path)]

    def _timeline(self, path, stream):
        time_base = Fraction(stream["time_base"])
        first = last = duration = previous = None
        count = 0

        def frame(line):
            nonlocal first, last, duration, previous, count
            fields = dict(part.split("=", 1) for part in line.split("|") if "=" in part)
            if "best_effort_timestamp" not in fields:
                return
            try:
                pts = int(fields["best_effort_timestamp"])
            except ValueError as error:
                raise ValueError("Video has undecodable source timestamps") from error
            if last is not None and pts < last:
                raise ValueError("Video presentation timestamps are not monotonic")
            first = pts if first is None else first
            previous, last = last, pts
            raw_duration = fields.get("pkt_duration", fields.get("duration"))
            duration = int(raw_duration) if raw_duration and raw_duration != "N/A" else None
            count += 1

        consume, pending = self._lines(frame)
        _, errors = self._run(self.ffprobe, ["-v", "error", "-protocol_whitelist", "file,pipe",
            "-select_streams", "v:0", "-show_frames", "-show_entries",
            "frame=best_effort_timestamp,pkt_duration,duration", "-of", "compact=p=0", str(path)], consume=consume)
        if pending:
            frame(pending.decode())
        if errors.strip():
            raise ValueError("Video decoding failed: " + errors[-2000:])
        if first is None:
            raise ValueError("Video contains no decodable timestamped frames")
        if not duration or duration <= 0:
            # An observed PTS delta is defensible when a container omits final packet duration.
            duration = last - previous if previous is not None and last > previous else None
        end = (last + duration) * time_base if duration else None
        if end is None:
            ticks = _number(stream.get("duration_ts"))
            if ticks is not None and ticks > 0:
                end = first * time_base + int(ticks) * time_base
        if end is None or end <= last * time_base:
            raise ValueError("Cannot establish video end from timestamps/duration")
        return {"first": first, "last": last, "end": end, "time_base": time_base,
                "frame_count": count, "end_method": "last decoded PTS plus packet duration or observed PTS delta; stream duration fallback"}

    def _boundaries(self, path, timeline):
        origin, end = timeline["first"] * timeline["time_base"], timeline["end"]
        boundaries = [{"time": origin, "score": None, "pts": timeline["first"]}]
        current = None

        def metadata(line):
            nonlocal current
            match = re.search(r"frame:\s*\d+\s+pts:\s*(-?\d+)\s+pts_time:([\d.eE+-]+)", line)
            if match:
                # Filter timestamps retain input stream time_base: no fps conversion.
                pts = int(match[1])
                current = (pts, pts * timeline["time_base"])
            elif line.startswith("lavfi.scene_score=") and current is not None:
                pts, timestamp = current
                score = float(line.split("=", 1)[1])
                if (timestamp - boundaries[-1]["time"] >= self.minimum_scene_seconds
                        and end - timestamp >= self.minimum_scene_seconds):
                    if len(boundaries) >= self.max_scenes:
                        raise ValueError(f"Scene count exceeds max_scenes={self.max_scenes}")
                    boundaries.append({"time": timestamp, "score": score, "pts": pts})
                current = None

        consume, pending = self._lines(metadata)
        base = timeline["time_base"]
        filters = (f"settb=expr={base.numerator}/{base.denominator},"
                   f"select='gt(scene,{self.scene_threshold})',metadata=print:file='pipe\\:1'")
        self._run(self.ffmpeg, [*self._input(path), "-map", "0:v:0", "-an", "-vf", filters,
                               "-fps_mode", "passthrough", "-f", "null", "-"], consume=consume)
        if pending:
            metadata(pending.decode())
        return boundaries

    @staticmethod
    def _showinfo(log):
        base = re.search(r"config in time_base:\s*(\d+/\d+)", log)
        if base is None:
            raise ValueError("FFmpeg omitted derived-frame time_base")
        time_base = Fraction(base[1])
        return [(int(pts), time_base) for pts in re.findall(r"\bn:\s*\d+\s+pts:\s*(-?\d+)\s+pts_time:", log)]

    def _representative(self, path, destination, target, scene_end, origin, scene_start=None):
        # Seek is relative to the stream origin; -copyts preserves source presentation time.
        arguments = self._input(path, verbose="info")
        seek = max(0.0, float(target - origin) - 1.0)
        arguments[arguments.index("-i"):arguments.index("-i")] = ["-ss", f"{seek:.9f}"]
        filters = (f"select='gte(t,{float(target):.12f})*lt(t,{float(scene_end):.12f})',"
                   "scale=640:640:force_original_aspect_ratio=decrease,setsar=1,showinfo")
        _, log = self._run(self.ffmpeg, [*arguments, "-map", "0:v:0", "-an", "-vf", filters,
            "-frames:v", "1", "-fps_mode", "passthrough", "-threads", "1", "-q:v", "3", str(destination)])
        if not destination.is_file() and scene_start is not None:
            return self._representative(path, destination, scene_start, scene_end, origin)
        stamps = self._showinfo(log)
        if not destination.is_file() or not stamps:
            raise ValueError("No representative frame decoded at scene target")
        pts, base = stamps[0]
        timestamp = pts * base
        if timestamp >= scene_end:
            raise ValueError("Representative frame fell outside its scene")
        if destination.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("Representative JPEG exceeds artifact size limit")
        with Image.open(destination) as image:
            image.verify()
        return {"path": str(destination), "source_timestamp_seconds": float(timestamp),
                "pts": pts, "time_base": _rational(base)}

    @staticmethod
    def _pixels(image):
        values = image.tobytes()
        histogram = image.histogram()
        laplacian = []
        for y in range(1, 63):
            for x in range(1, 63):
                i = y * 64 + x
                laplacian.append(4 * values[i] - values[i - 1] - values[i + 1] - values[i - 64] - values[i + 64])
        mean = sum(laplacian) / len(laplacian)
        return {"mean_luma_8bit": ImageStat.Stat(image).mean[0],
                "dark_pixel_fraction": sum(histogram[:17]) / 4096,
                "bright_pixel_fraction": sum(histogram[235:]) / 4096,
                "black_pixel_fraction": sum(histogram[:9]) / 4096,
                "laplacian_variance_blur_proxy": sum((value - mean) ** 2 for value in laplacian) / len(laplacian)}

    def _samples(self, path, timeline, boundaries):
        origin = timeline["first"] * timeline["time_base"]
        interval = float((timeline["end"] - origin) / (MAX_SAMPLES - 1))
        filters = (f"select='isnan(prev_selected_t)+gte(t-prev_selected_t,{interval:.12f})',"
                   "scale=64:64,format=gray,showinfo")
        raw, log = self._run(self.ffmpeg, [*self._input(path, verbose="info"), "-map", "0:v:0", "-an",
            "-vf", filters, "-fps_mode", "passthrough", "-threads", "1", "-f", "rawvideo", "-"],
            max_output=MAX_SAMPLES * 4096)
        stamps = self._showinfo(log)
        if not stamps or len(raw) != 4096 * len(stamps):
            raise ValueError("Sampled frame pixels/timestamps do not match")
        samples, pairs, shifts = [], [], []
        previous = previous_time = None
        for index, (pts, base) in enumerate(stamps):
            image = Image.frombytes("L", (64, 64), raw[index * 4096:(index + 1) * 4096])
            timestamp = pts * base
            samples.append({"source_timestamp_seconds": float(timestamp), "pts": pts,
                            "time_base": _rational(base), **self._pixels(image)})
            if previous is not None and not any(previous_time < boundary["time"] <= timestamp for boundary in boundaries[1:]):
                difference = ImageStat.Stat(ImageChops.difference(previous, image)).mean[0]
                # Coarse global translation search, excluding wrapped edges and known cuts.
                candidates = []
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        shifted = ImageChops.offset(previous, dx, dy).crop((2, 2, 62, 62))
                        error = ImageStat.Stat(ImageChops.difference(shifted, image.crop((2, 2, 62, 62)))).mean[0]
                        candidates.append((error, abs(dx) + abs(dy), dx, dy))
                _, _, dx, dy = min(candidates)
                shifts.append((dx, dy))
                pairs.append({"start_seconds": float(previous_time), "end_seconds": float(timestamp),
                              "mean_absolute_luma_difference": difference,
                              "frozen_proxy": difference <= 1.0, "translation_pixels": [dx, dy]})
            else:
                shifts.append(None)
            previous, previous_time = image, timestamp
        acceleration = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(shifts, shifts[1:])
                        if a is not None and b is not None]
        return {"status": "measured", "sample_count": len(samples), "samples": samples, "temporal_pairs": pairs,
                "shakiness_translation_change_proxy": sum(acceleration) / len(acceleration) if acceleration else None,
                "methodology": {"sampling": "at most 64 approximately uniform source-time frames; not exhaustive",
                    "pixels": "64x64 decoded 8-bit grayscale, rotation applied; spatial aspect deliberately normalized",
                    "blur": "variance of discrete Laplacian; lower may indicate blur or naturally flat content",
                    "exposure": "pixel fractions <=16, >=235; black <=8; not calibrated photometry",
                    "frozen": "sample-pair mean absolute luma difference <=1; does not prove continuous freeze",
                    "motion": "sample-pair luma difference and best integer translation within +/-2 pixels; known cuts excluded",
                    "shakiness": "mean change in consecutive estimated translations; subject motion/aliasing confound it, not stabilization judgment"}}

    def _audio(self, path, stream):
        if stream is None:
            return {"status": "no_audio", "methodology": "No audio stream present"}
        channels = int(stream["channels"])
        if not 1 <= channels <= 32:
            raise ValueError("Audio exceeds supported 1..32 channel resource limit")
        count, total_squared, peak, silent, clipped = 0, 0.0, 0.0, 0, 0
        pending = bytearray()

        def consume(chunk):
            nonlocal count, total_squared, peak, silent, clipped
            pending.extend(chunk)
            length = len(pending) - len(pending) % 4
            samples = array("f")
            samples.frombytes(pending[:length])
            del pending[:length]
            if sys.byteorder != "little":
                samples.byteswap()
            for value in samples:
                if not math.isfinite(value):
                    raise ValueError("Audio contains nonfinite decoded samples")
                magnitude = abs(value)
                total_squared += value * value
                peak = max(peak, magnitude)
                silent += magnitude <= 0.001
                clipped += magnitude >= 1 - 1 / 32768
            count += len(samples)

        self._run(self.ffmpeg, [*self._input(path), "-map", "0:a:0", "-vn", "-c:a", "pcm_f32le",
                               "-f", "f32le", "-"], consume=consume)
        if not count or pending or count % channels:
            raise ValueError("Audio stream contains no complete decodable samples")
        rms = math.sqrt(total_squared / count)
        sample_rate = int(stream["sample_rate"])
        return {"status": "measured", "stream_index": stream["index"], "channels": channels,
                "sample_rate_hz": sample_rate, "sample_values": count,
                "decoded_duration_seconds": count / channels / sample_rate,
                "rms_amplitude": rms, "rms_dbfs": 20 * math.log10(rms) if rms else None,
                "digital_silence": rms == 0, "peak_amplitude": peak,
                "peak_dbfs": 20 * math.log10(peak) if peak else None,
                "silence_sample_fraction": silent / count, "clipping_sample_fraction": clipped / count,
                "methodology": "All first-audio-stream decoded float samples, all channels pooled; silence abs<=0.001 (-60 dBFS), clipping proxy abs>=1-1/32768; no loudness/LUFS or audible-clipping claim; null dBFS denotes digital silence"}

    def extract(self, path: Path, output_dir: Path) -> dict:
        path = Path(path).resolve(strict=True)
        if not path.is_file():
            raise ValueError("Video source must be a regular file")
        provenance = self.provenance()
        raw, errors = self._run(self.ffprobe, ["-v", "error", "-protocol_whitelist", "file,pipe",
            "-show_streams", "-show_format", "-of", "json", str(path)])
        if errors.strip():
            raise ValueError("Video probe failed: " + errors[-2000:])
        probe = json.loads(raw)
        videos = [stream for stream in probe.get("streams", []) if stream.get("codec_type") == "video"
                  and not stream.get("disposition", {}).get("attached_pic")]
        if not videos:
            raise ValueError("Source has no video stream")
        video = videos[0]
        # The selected stream is v:0 throughout; reject ambiguous cover-art-first files.
        if video != next(stream for stream in probe["streams"] if stream.get("codec_type") == "video"):
            raise ValueError("Cover-art-first video stream ordering is unsupported")
        if int(video.get("width", 0)) * int(video.get("height", 0)) > 16_777_216:
            raise ValueError("Video exceeds 16777216 decoded pixel resource limit")
        timeline = self._timeline(path, video)
        boundaries = self._boundaries(path, timeline)
        output_dir = Path(output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="video-", dir=output_dir))
        try:
            origin = timeline["first"] * timeline["time_base"]
            scenes = []
            for index, boundary in enumerate(boundaries):
                end = boundaries[index + 1]["time"] if index + 1 < len(boundaries) else timeline["end"]
                target = min((boundary["time"] + end) / 2, timeline["last"] * timeline["time_base"])
                scenes.append({"index": index, "start_seconds": float(boundary["time"]), "end_seconds": float(end),
                    "end_rational_seconds": _rational(end),
                    "start_pts": boundary["pts"], "time_base": _rational(timeline["time_base"]),
                    "boundary_evidence": {"method": "source_start" if index == 0 else "ffmpeg_scene_score",
                        "scene_score": boundary["score"], "threshold": self.scene_threshold,
                        "confidence": None, "confidence_methodology": "Raw scene score is evidence strength, not calibrated probability; minimum spacing merges short candidates"},
                    "representative_frame": self._representative(path, work / f"scene-{index:04d}.jpg", target, end, origin, boundary["time"])})
            transfer = video.get("color_transfer", "unknown")
            hdr = transfer in ("smpte2084", "arib-std-b67")
            return {"schema_version": "1.0", "provenance": provenance,
                "source_timing": {"semantics": "absolute source presentation seconds, not normalized clip time",
                    "origin_seconds": float(origin), "origin_pts": timeline["first"],
                    "time_base": _rational(timeline["time_base"]), "end_seconds": float(timeline["end"]),
                    "end_rational_seconds": _rational(timeline["end"]),
                    "decoded_frame_count": timeline["frame_count"], "end_method": timeline["end_method"]},
                "derivatives": {"rotation": "FFmpeg default autorotation from source display matrix/tags",
                    "source_color_transfer": transfer, "hdr_detected": hdr,
                    "color_handling": "8-bit FFmpeg conversion only; no HDR tone mapping. JPEG and luma proxies are not HDR fidelity/photometry measurements" if hdr else "8-bit FFmpeg conversion; preview only, not color-managed fidelity measurement"},
                "scenes": scenes, "measurements": {"frames": self._samples(path, timeline, boundaries),
                    "audio": self._audio(path, next((stream for stream in probe["streams"] if stream.get("codec_type") == "audio"), None))}}
        except BaseException:
            shutil.rmtree(work)
            raise
