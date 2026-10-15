"""Bounded local audio evidence; signal energy is not evidence of speech."""

from array import array
from contextlib import contextmanager
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile
import wave

from .video_analysis import VideoTechnicalExtractor


ALGORITHM_VERSION = "audio-technical-1"
MAX_CHANNELS = 32
MAX_SAMPLE_RATE = 384000
MAX_DECODED_BYTES = 1024 * 1024 * 1024
MAX_TIMELINE_BYTES = 32 * 1024 * 1024
MAX_TIMELINE_FRAMES = 2_000_000
MAX_SEGMENTS = 4096
SILENCE_THRESHOLD = 0.001
MINIMUM_SILENCE_SECONDS = 0.1


class AudioTechnicalExtractor:
    def __init__(self, ffmpeg="ffmpeg", ffprobe="ffprobe", timeout=30, max_audio_seconds=1800):
        if isinstance(max_audio_seconds, bool) or not math.isfinite(max_audio_seconds) or max_audio_seconds <= 0:
            raise ValueError("max_audio_seconds must be finite and positive")
        self.ffmpeg, self.ffprobe = ffmpeg, ffprobe
        self.max_audio_seconds = max_audio_seconds
        # Share the subprocess timeout, concurrent pipe draining and cancellation policy.
        self._runner = VideoTechnicalExtractor(ffmpeg=ffmpeg, ffprobe=ffprobe, timeout=timeout)
        self._versions = None

    def provenance(self):
        if self._versions is None:
            self._versions = {
                name: self._runner._run(executable, ["-version"])[0].decode().splitlines()[0]
                for name, executable in (("ffmpeg", self.ffmpeg), ("ffprobe", self.ffprobe))
            }
        return {
            "algorithm": ALGORITHM_VERSION, "tools": dict(self._versions),
            "max_audio_seconds": self.max_audio_seconds,
            "silence_amplitude_threshold": SILENCE_THRESHOLD,
            "minimum_silence_seconds": MINIMUM_SILENCE_SECONDS,
            "resource_limits": {"audio_channels": MAX_CHANNELS, "sample_rate_hz": MAX_SAMPLE_RATE,
                                "decoded_bytes": MAX_DECODED_BYTES, "timeline_bytes": MAX_TIMELINE_BYTES,
                                "timeline_frames": MAX_TIMELINE_FRAMES, "segments": MAX_SEGMENTS,
                                "probe_payload_bytes": 2097152, "stderr_bytes_per_process": 262144,
                                "metadata_line_bytes": 16384},
            "timing_method": "First decoded source PTS plus decoded sample count; reject timestamp discontinuities exceeding one source time-base tick",
            "signal_method": "First audio stream; decoded float samples, all channels pooled for RMS/peak/clipping; no loudness/LUFS or audible-clipping claim",
            "segment_method": "All channels abs<=0.001 for at least 0.1 seconds marks silence; complement is nonsilent, never speech; no semantic inference",
        }

    def cache_key(self):
        return hashlib.sha256(json.dumps(self.provenance(), sort_keys=True).encode()).hexdigest()

    def _probe(self, path):
        path = Path(path).resolve(strict=True)
        if not path.is_file():
            raise ValueError("Audio source must be a regular file")
        raw, errors = self._runner._run(self.ffprobe, [
            "-v", "error", "-protocol_whitelist", "file,pipe", "-show_streams",
            "-show_entries", "stream=index,codec_type,channels,sample_rate,time_base,start_time",
            "-of", "json", str(path)])
        if errors.strip():
            raise ValueError("Audio probe failed: " + errors[-2000:])
        streams = json.loads(raw).get("streams", [])
        stream = next((item for item in streams if item.get("codec_type") == "audio"), None)
        if stream is None:
            if any(item.get("codec_type") == "video" for item in streams):
                return path, None
            raise ValueError("Source has no audio stream")
        try:
            channels, rate = int(stream["channels"]), int(stream["sample_rate"])
            base = Fraction(stream["time_base"])
            index = int(stream["index"])
            start_time = stream.get("start_time", "0")
            try:
                start = Fraction(start_time)
            except ValueError:
                start = Fraction(0)
        except (KeyError, ValueError, TypeError, ZeroDivisionError) as error:
            raise ValueError("Audio has invalid stream parameters") from error
        if not 1 <= channels <= MAX_CHANNELS or not 1 <= rate <= MAX_SAMPLE_RATE or base <= 0 or index < 0:
            raise ValueError("Audio exceeds supported channel/sample-rate/time-base resource limits")
        return path, {"index": index, "channels": channels, "sample_rate": rate,
                      "time_base": base, "start_time": start}

    def _timeline(self, path, stream):
        origin = None
        samples = frames = payload = 0
        pending = bytearray()
        rate, base, start = stream["sample_rate"], stream["time_base"], stream["start_time"]
        limit = Fraction(str(self.max_audio_seconds))

        def frame(line):
            nonlocal origin, samples, frames
            if not line.strip():
                return
            fields = dict(part.split("=", 1) for part in line.split("|") if "=" in part)
            try:
                pts = int(fields.get("pts", fields.get("best_effort_timestamp", "")))
                count = int(fields["nb_samples"])
                channels = int(fields.get("channels", stream["channels"]))
                sample_rate = int(fields.get("sample_rate", rate))
            except (KeyError, ValueError) as error:
                raise ValueError("Audio contains undecodable source timestamps or sample counts") from error
            if count <= 0 or channels != stream["channels"] or sample_rate != rate:
                raise ValueError("Audio changes channel/sample-rate parameters or has invalid sample counts")
            # The first decoded frame PTS already includes the container start_time; use it directly.
            timestamp = pts * base
            if origin is None:
                origin = timestamp
            # Compare against the cumulative sample clock, never accumulate rounded PTS deltas.
            if abs(timestamp - (origin + Fraction(samples, rate))) > base:
                raise ValueError("Audio timestamp discontinuity exceeds source time-base precision")
            samples += count
            frames += 1
            if Fraction(samples, rate) > limit:
                raise ValueError("Audio duration exceeds max_audio_seconds")
            if frames > MAX_TIMELINE_FRAMES:
                raise ValueError("Audio timeline exceeds bounded frame limit")
            if samples * stream["channels"] * 4 > MAX_DECODED_BYTES:
                raise ValueError("Audio decoded output exceeds bounded payload limit")

        def consume(chunk):
            nonlocal payload
            payload += len(chunk)
            if payload > MAX_TIMELINE_BYTES:
                raise ValueError("Audio timeline exceeds bounded payload limit")
            pending.extend(chunk)
            while (end := pending.find(b"\n")) >= 0:
                if end > 16384:
                    raise ValueError("Audio metadata line exceeds limit")
                line = bytes(pending[:end]).decode("utf-8", "strict")
                del pending[:end + 1]
                frame(line)
            if len(pending) > 16384:
                raise ValueError("Audio metadata line exceeds limit")

        _, errors = self._runner._run(self.ffprobe, [
            "-v", "error", "-protocol_whitelist", "file,pipe", "-select_streams", "a:0",
            "-show_frames", "-show_entries", "frame=pts,best_effort_timestamp,nb_samples,channels,sample_rate",
            "-of", "compact=p=0", str(path)], consume=consume)
        if pending:
            frame(pending.decode("utf-8", "strict"))
        if errors.strip():
            raise ValueError("Audio decoding failed: " + errors[-2000:])
        if origin is None or not samples:
            raise ValueError("Audio contains no decodable timestamped samples")
        duration = Fraction(samples, rate)
        return {"origin_seconds": float(origin), "end_seconds": float(origin + duration),
                "duration_seconds": float(duration), "stream_index": stream["index"],
                "sample_rate_hz": rate, "sample_frames": samples}

    def _measure(self, path, stream, timing):
        channels, rate = stream["channels"], stream["sample_rate"]
        count = silent_values = clipped = 0
        total_squared = peak = 0.0
        pending = bytearray()
        segments = []
        silence_start = None
        cursor = 0
        minimum = math.ceil(MINIMUM_SILENCE_SECONDS * rate)
        origin = timing["origin_seconds"]

        def append(kind, start, end):
            if start == end:
                return
            if len(segments) >= MAX_SEGMENTS:
                raise ValueError("Audio exceeds bounded segment limit")
            segments.append({"kind": kind, "start_seconds": origin + start / rate,
                             "end_seconds": origin + end / rate, "duration_seconds": (end - start) / rate})

        def finish_silence(end):
            nonlocal cursor, silence_start
            if silence_start is not None and end - silence_start >= minimum:
                append("nonsilent", cursor, silence_start)
                append("silence", silence_start, end)
                cursor = end
            silence_start = None

        def consume(chunk):
            nonlocal count, silent_values, clipped, total_squared, peak, silence_start
            pending.extend(chunk)
            length = len(pending) - len(pending) % (4 * channels)
            values = array("f")
            values.frombytes(pending[:length])
            del pending[:length]
            if sys.byteorder != "little":
                values.byteswap()
            if (count + len(values)) * 4 > MAX_DECODED_BYTES:
                raise ValueError("Audio decoded output exceeds bounded payload limit")
            if (count + len(values)) // channels > timing["sample_frames"]:
                raise ValueError("Audio decoded sample count exceeds probed timeline")
            for position in range(0, len(values), channels):
                quiet = True
                for channel in range(channels):
                    value = values[position + channel]
                    if not math.isfinite(value):
                        raise ValueError("Audio contains nonfinite decoded samples")
                    magnitude = abs(value)
                    total_squared += value * value
                    peak = max(peak, magnitude)
                    silent_values += magnitude <= SILENCE_THRESHOLD
                    clipped += magnitude >= 1 - 1 / 32768
                    quiet = quiet and magnitude <= SILENCE_THRESHOLD
                frame_index = (count + position) // channels
                if quiet:
                    if silence_start is None:
                        silence_start = frame_index
                elif silence_start is not None:
                    finish_silence(frame_index)
            count += len(values)

        self._runner._run(self.ffmpeg, [*self._runner._input(path), "-map", "0:a:0", "-vn", "-sn", "-dn",
                                       "-c:a", "pcm_f32le", "-f", "f32le", "-"], consume=consume)
        if pending or count != timing["sample_frames"] * channels:
            raise ValueError("Audio decoded sample count does not match source timeline")
        finish_silence(count // channels)
        append("nonsilent", cursor, count // channels)
        rms = math.sqrt(total_squared / count)
        return {"channels": channels, "sample_rate_hz": rate, "sample_values": count,
                "sample_frames": count // channels, "decoded_duration_seconds": timing["duration_seconds"],
                "rms_amplitude": rms, "rms_dbfs": 20 * math.log10(rms) if rms else None,
                "peak_amplitude": peak, "peak_dbfs": 20 * math.log10(peak) if peak else None,
                "digital_silence": rms == 0, "silence_sample_fraction": silent_values / count,
                "clipping_sample_fraction": clipped / count}, segments

    def extract(self, path: Path):
        path, stream = self._probe(path)
        provenance = self.provenance()
        if stream is None:
            return {"schema_version": "1.0", "provenance": provenance, "status": "no_audio",
                    "source_timing": None, "measurements": {}, "segments": []}
        timing = self._timeline(path, stream)
        measurements, segments = self._measure(path, stream, timing)
        return {"schema_version": "1.0", "provenance": provenance, "status": "measured",
                "source_timing": timing, "measurements": measurements, "segments": segments}


@contextmanager
def decoded_audio(path, ffmpeg="ffmpeg", ffprobe="ffprobe", timeout=30, max_audio_seconds=1800):
    """Yield a temporary mono 16 kHz signed-16 PCM WAV and absolute source timing.

    The source is read only. Both decoding failures and caller cancellation remove
    the derivative. Timestamp gaps/overlaps are rejected, never closed silently.
    """
    extractor = AudioTechnicalExtractor(ffmpeg, ffprobe, timeout, max_audio_seconds)
    path, stream = extractor._probe(path)
    if stream is None:
        raise ValueError("Source has no audio stream")
    timing = extractor._timeline(path, stream)
    count = 0
    maximum = min(MAX_DECODED_BYTES, math.ceil(max_audio_seconds * 16000) * 2)
    with tempfile.TemporaryDirectory(prefix="storylens-audio-") as directory:
        destination = Path(directory) / "audio.wav"
        with wave.open(str(destination), "wb") as output:
            output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))

            def consume(chunk):
                nonlocal count
                count += len(chunk)
                if count > maximum:
                    raise ValueError("Decoded audio exceeds bounded payload/duration limit")
                output.writeframesraw(chunk)

            extractor._runner._run(ffmpeg, [*extractor._runner._input(path), "-map", "0:a:0", "-vn", "-sn", "-dn",
                                           "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-f", "s16le", "-"],
                                   consume=consume)
        expected = Fraction(timing["sample_frames"] * 16000, stream["sample_rate"])
        if not count or count % 2 or abs(Fraction(count, 2) - expected) > 1:
            raise ValueError("Decoded audio sample count does not match source timeline")
        yield destination, {"origin_seconds": timing["origin_seconds"],
                            "end_seconds": timing["end_seconds"],
                            "duration_seconds": timing["duration_seconds"],
                            "stream_index": timing["stream_index"],
                            "sample_rate_hz": 16000}
