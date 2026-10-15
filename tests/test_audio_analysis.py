"""Real generated audio fixtures; no uploaded/private media."""

import hashlib
import shutil
import struct
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path

from storylens.audio_analysis import AudioTechnicalExtractor, decoded_audio


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class AudioTechnicalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.extractor = AudioTechnicalExtractor()

    def ffmpeg(self, *arguments):
        subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", *map(str, arguments)],
                       check=True, capture_output=True, timeout=15)

    def tone(self, name="tone.wav", frequency=1000, seconds=2, offset=0, stereo=False):
        """Generate a sine tone. offset>0 produces a shifted, trimmed MKA with absolute origin."""
        path = self.base / name
        channels = 2 if stereo else 1
        self.ffmpeg("-f", "lavfi", "-i", f"aevalsrc=sin(2*PI*{frequency}*t):s=16000:d={seconds + offset}",
                    "-ac", str(channels), "-ar", "16000", "-c:a", "pcm_s16le", path)
        if offset:
            shifted = (self.base / f"offset_{name}").with_suffix(".mka")
            self.ffmpeg("-ss", str(offset), "-t", str(seconds), "-i", path,
                        "-output_ts_offset", str(offset),
                        "-c:a", "pcm_s16le", "-ar", "16000", shifted)
            return shifted
        return path

    def synthetic(self, name="synthetic.wav", values=None, offset=0):
        """Create a WAV from a list of int16 values in signed 16-bit range."""
        path = self.base / name
        values = values or []
        with wave.open(str(path), "wb") as output:
            output.setparams((1, 2, 16000, len(values), "NONE", "not compressed"))
            output.writeframes(struct.pack(f"<{len(values)}h", *values))
        if offset == 0:
            return path
        shifted = self.base / f"shifted_{name}"
        self.ffmpeg("-copyts", "-i", path, "-ss", str(offset), "-c:a", "copy", shifted)
        return shifted

    def test_extract_smoke_tone_and_source_unchanged(self):
        source = self.tone(seconds=2)
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        result = self.extractor.extract(source)
        self.assertEqual(result["schema_version"], "1.0")
        self.assertEqual(result["status"], "measured")
        self.assertAlmostEqual(result["source_timing"]["origin_seconds"], 0)
        self.assertAlmostEqual(result["source_timing"]["end_seconds"], 2)
        self.assertEqual(result["source_timing"]["sample_rate_hz"], 16000)
        self.assertEqual(result["measurements"]["sample_frames"], 32000)
        self.assertEqual(result["measurements"]["channels"], 1)
        self.assertGreater(result["measurements"]["rms_dbfs"], -22)
        self.assertGreater(result["measurements"]["peak_amplitude"], 0.12)
        self.assertEqual(result["measurements"]["digital_silence"], False)
        self.assertTrue(any(s["kind"] == "nonsilent" for s in result["segments"]))
        self.assertEqual(before, hashlib.sha256(source.read_bytes()).hexdigest())

    def test_extract_silence_clipping_and_segments(self):
        # 0.5s silence, 0.5s full-scale, 0.5s silence
        samples = 16000
        values = [0] * (samples // 2) + [32767, -32768] * (samples // 4) + [0] * (samples // 2)
        source = self.synthetic(values=values)
        result = self.extractor.extract(source)
        self.assertAlmostEqual(result["source_timing"]["duration_seconds"], 1.5)
        self.assertAlmostEqual(result["measurements"]["silence_sample_fraction"], 2 / 3, places=2)
        self.assertAlmostEqual(result["measurements"]["clipping_sample_fraction"], 1 / 3, places=2)
        self.assertEqual(len(result["segments"]), 3)
        self.assertEqual([s["kind"] for s in result["segments"]], ["silence", "nonsilent", "silence"])
        self.assertEqual(result["measurements"]["digital_silence"], False)

    def test_extract_no_audio_video_is_explicit(self):
        video = self.base / "video.mp4"
        self.ffmpeg("-f", "lavfi", "-i", "color=black:s=80x48:r=10:d=1", "-an", "-c:v", "mpeg4", video)
        result = self.extractor.extract(video)
        self.assertEqual(result["status"], "no_audio")
        self.assertIsNone(result["source_timing"])
        self.assertEqual(result["measurements"], {})
        self.assertEqual(result["segments"], [])

    def test_extract_no_audio_raises(self):
        empty = self.base / "empty.wav"
        with wave.open(str(empty), "wb") as output:
            output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        with self.assertRaisesRegex(ValueError, "no decodable"):
            self.extractor.extract(empty)

    def test_nonzero_origin_retains_absolute_source_time(self):
        source = self.tone(seconds=1, offset=5)
        result = self.extractor.extract(source)
        self.assertEqual(result["source_timing"]["origin_seconds"], 5)
        self.assertEqual(result["source_timing"]["end_seconds"], 6)
        self.assertEqual(result["source_timing"]["duration_seconds"], 1)
        self.assertTrue(all(s["start_seconds"] >= 5 for s in result["segments"]))

    def test_max_audio_seconds_bound_hard_fails(self):
        extractor = AudioTechnicalExtractor(max_audio_seconds=1)
        source = self.tone(seconds=2)
        with self.assertRaisesRegex(ValueError, "max_audio_seconds"):
            extractor.extract(source)

    def test_decoded_audio_context_yields_mono_16000_pcm(self):
        source = self.tone(seconds=1, stereo=True)
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        with decoded_audio(source) as (wav_path, timing):
            self.assertTrue(wav_path.suffix, ".wav")
            self.assertTrue(wav_path.exists())
            with wave.open(str(wav_path), "rb") as handle:
                self.assertEqual(handle.getnchannels(), 1)
                self.assertEqual(handle.getframerate(), 16000)
                self.assertEqual(handle.getsampwidth(), 2)
                self.assertEqual(handle.getnframes(), 16000)
            self.assertEqual(timing["sample_rate_hz"], 16000)
            self.assertEqual(timing["origin_seconds"], 0)
            self.assertEqual(timing["end_seconds"], 1)
        self.assertFalse(wav_path.exists())
        self.assertEqual(before, hashlib.sha256(source.read_bytes()).hexdigest())

    def test_decoded_audio_cleans_up_on_error(self):
        source = self.tone(seconds=1)
        captured = None
        with self.assertRaises(RuntimeError):
            with decoded_audio(source) as (wav_path, _):
                captured = wav_path
                raise RuntimeError("caller abort")
        self.assertFalse(captured.exists())

    def test_decoded_audio_nonzero_origin(self):
        source = self.tone(seconds=1, offset=7)
        with decoded_audio(source) as (_, timing):
            self.assertEqual(timing["origin_seconds"], 7)
            self.assertEqual(timing["duration_seconds"], 1)

    def test_decoded_audio_rejects_video_without_audio(self):
        video = self.base / "video.mp4"
        self.ffmpeg("-f", "lavfi", "-i", "color=black:s=80x48:r=10:d=1", "-an", "-c:v", "mpeg4", video)
        with self.assertRaisesRegex(ValueError, "no audio stream"):
            with decoded_audio(video) as _:
                pass

    def test_duration_bound_catches_output_payload(self):
        extractor = AudioTechnicalExtractor(max_audio_seconds=2)
        source = self.tone(seconds=3)
        with self.assertRaisesRegex(ValueError, "max_audio_seconds"):
            extractor.extract(source)

    def test_channel_and_sample_parameters_rejected(self):
        source = self.base / "weird.wav"
        with wave.open(str(source), "wb") as output:
            output.setparams((1, 2, 500000, 16000, "NONE", "not compressed"))
            output.writeframes(b"\x00" * (16000 * 2))
        with self.assertRaisesRegex(ValueError, "sample-rate"):
            self.extractor.extract(source)

    def test_constructor_validates_max_seconds(self):
        with self.assertRaises(ValueError):
            AudioTechnicalExtractor(max_audio_seconds=0)
        with self.assertRaises(ValueError):
            AudioTechnicalExtractor(max_audio_seconds=float("inf"))
        with self.assertRaises(ValueError):
            AudioTechnicalExtractor(max_audio_seconds=-1)

    def test_cache_key_and_provenance_stable(self):
        a = self.extractor.cache_key()
        b = self.extractor.cache_key()
        self.assertEqual(a, b)
        self.assertIn("audio-technical-1", self.extractor.provenance()["algorithm"])

    def test_json_serializable(self):
        source = self.tone(seconds=1)
        import json
        result = self.extractor.extract(source)
        json.dumps(result, allow_nan=False)

    def test_discontinuity_rejected(self):
        # Two concatenated sine segments with a 2-second gap in PTS.
        source = self.base / "gap.mka"
        self.ffmpeg("-f", "lavfi", "-i", "sine=frequency=1000:duration=1",
                    "-f", "lavfi", "-i", "sine=frequency=1000:duration=1",
                    "-filter_complex",
                    "[0:a]asetpts=PTS[aa];[1:a]asetpts=PTS+48000[bb];[aa][bb]concat=n=2:v=0:a=1[a]",
                    "-map", "[a]", "-c:a", "pcm_s16le", "-ar", "48000", source)
        with self.assertRaisesRegex(ValueError, "discontinuity"):
            self.extractor.extract(source)


if __name__ == "__main__":
    unittest.main()
