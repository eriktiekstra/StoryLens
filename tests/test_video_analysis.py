"""Real, locally generated FFmpeg fixtures; no uploaded/private media."""

from fractions import Fraction
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

from PIL import Image

from storylens.video_analysis import VideoTechnicalExtractor


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class VideoTechnicalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.output = self.base / "artifacts"
        self.extractor = VideoTechnicalExtractor()

    def ffmpeg(self, *arguments):
        subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", *map(str, arguments)],
                       check=True, capture_output=True, timeout=15)

    def static(self, name="static.mp4", color="black", duration=1):
        path = self.base / name
        self.ffmpeg("-f", "lavfi", "-i", f"color={color}:s=80x48:r=10:d={duration}",
                    "-c:v", "mpeg4", path)
        return path

    def cut(self, offset=0):
        path = self.base / "cut.mkv"
        self.ffmpeg("-f", "lavfi", "-i", "color=black:s=80x48:r=10:d=1",
                    "-f", "lavfi", "-i", "color=white:s=80x48:r=10:d=1",
                    "-filter_complex", f"[0:v][1:v]concat=n=2:v=1:a=0,setpts=PTS+{offset}/TB[v]",
                    "-map", "[v]", "-fps_mode", "passthrough", "-c:v", "mpeg4", path)
        return path

    def assert_artifacts(self, result):
        for scene in result["scenes"]:
            representative = scene["representative_frame"]
            path = Path(representative["path"])
            self.assertTrue(path.is_absolute())
            self.assertTrue(path.is_relative_to(self.output))
            with Image.open(path) as image:
                self.assertEqual(image.format, "JPEG")
                self.assertLessEqual(max(image.size), 640)
            rational = representative["time_base"]
            timestamp = representative["pts"] * Fraction(rational["numerator"], rational["denominator"])
            self.assertAlmostEqual(float(timestamp), representative["source_timestamp_seconds"])
            self.assertGreaterEqual(float(timestamp), scene["start_seconds"])
            self.assertLess(float(timestamp), scene["end_seconds"])
        json.dumps(result, allow_nan=False)

    def test_static_smoke_source_unchanged_and_real_jpeg(self):
        source = self.static()
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        result = self.extractor.extract(source, self.output)
        self.assertEqual(result["schema_version"], "1.0")
        self.assertEqual(len(result["scenes"]), 1)
        self.assertEqual(result["scenes"][0]["start_seconds"], 0)
        self.assertEqual(result["scenes"][0]["end_seconds"], 1)
        self.assertEqual(result["measurements"]["audio"]["status"], "no_audio")
        self.assertEqual(before, hashlib.sha256(source.read_bytes()).hexdigest())
        self.assert_artifacts(result)

    def test_hard_cut_detects_real_boundary_and_distinct_frames(self):
        result = self.extractor.extract(self.cut(), self.output)
        self.assertEqual(len(result["scenes"]), 2)
        self.assertAlmostEqual(result["scenes"][1]["start_seconds"], 1)
        self.assertAlmostEqual(result["scenes"][0]["end_seconds"], 1)
        evidence = result["scenes"][1]["boundary_evidence"]
        self.assertGreater(evidence["scene_score"], 0.3)
        self.assertIsNone(evidence["confidence"])
        means = []
        for scene in result["scenes"]:
            with Image.open(scene["representative_frame"]["path"]) as image:
                means.append(sum(image.convert("L").tobytes()) / (image.width * image.height))
        self.assertLess(means[0], 10)
        self.assertGreater(means[1], 240)
        self.assert_artifacts(result)

    def test_nonzero_origin_retains_absolute_source_time(self):
        result = self.extractor.extract(self.cut(offset=5), self.output)
        self.assertEqual(result["source_timing"]["origin_seconds"], 5)
        self.assertEqual([scene["start_seconds"] for scene in result["scenes"]], [5, 6])
        self.assertEqual(result["scenes"][-1]["end_seconds"], 7)
        self.assert_artifacts(result)

    def test_vfr_samples_use_real_pts_not_frame_number_over_fps(self):
        source = self.base / "variable.mkv"
        self.ffmpeg("-f", "lavfi", "-i", "testsrc2=s=80x48:r=10:d=2",
                    "-vf", "select='not(eq(n,2)+eq(n,3)+eq(n,7))'", "-fps_mode", "passthrough",
                    "-c:v", "mpeg4", source)
        result = self.extractor.extract(source, self.output)
        samples = result["measurements"]["frames"]["samples"]
        times = [sample["source_timestamp_seconds"] for sample in samples]
        self.assertIn(0.4, times)
        self.assertNotIn(0.2, times)
        self.assertNotIn(0.3, times)
        self.assertEqual(result["source_timing"]["decoded_frame_count"], 17)
        self.assertAlmostEqual(result["source_timing"]["end_seconds"], 2)
        self.assertGreater(max(b - a for a, b in zip(times, times[1:])), 0.2)
        self.assert_artifacts(result)

    def test_rotation_display_matrix_respected(self):
        original = self.static(color="red")
        rotated = self.base / "rotated.mp4"
        self.ffmpeg("-display_rotation:v:0", "90", "-i", original, "-c", "copy", rotated)
        result = self.extractor.extract(rotated, self.output)
        with Image.open(result["scenes"][0]["representative_frame"]["path"]) as image:
            self.assertGreater(image.height, image.width)
            self.assertAlmostEqual(image.height / image.width, 80 / 48)
        self.assert_artifacts(result)

    def test_frame_proxies_black_exposure_blur_and_frozen(self):
        result = self.extractor.extract(self.static(), self.output)
        measurements = result["measurements"]["frames"]
        self.assertTrue(measurements["temporal_pairs"])
        for sample in measurements["samples"]:
            self.assertGreater(sample["black_pixel_fraction"], 0.99)
            self.assertGreater(sample["dark_pixel_fraction"], 0.99)
            self.assertEqual(sample["laplacian_variance_blur_proxy"], 0)
        self.assertTrue(all(pair["frozen_proxy"] for pair in measurements["temporal_pairs"]))
        self.assertEqual(measurements["shakiness_translation_change_proxy"], 0)

    def test_audio_silence_and_full_scale_clipping_measurements(self):
        source = self.static(duration=2)
        for name, values, expected_silence, expected_clip in (
            ("silent", [0] * 16000, 1.0, 0.0),
            ("clipped", [0] * 8000 + [32767, -32768] * 4000, 0.5, 0.5),
        ):
            with self.subTest(name=name):
                audio = self.base / f"{name}.wav"
                with wave.open(str(audio), "wb") as output:
                    output.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
                    output.writeframes(struct.pack(f"<{len(values)}h", *values))
                muxed = self.base / f"{name}.mkv"
                self.ffmpeg("-i", source, "-i", audio, "-c:v", "copy", "-c:a", "pcm_s16le", muxed)
                measured = self.extractor.extract(muxed, self.output)["measurements"]["audio"]
                self.assertEqual(measured["status"], "measured")
                self.assertEqual(measured["sample_values"], 16000)
                self.assertEqual(measured["decoded_duration_seconds"], 2)
                self.assertAlmostEqual(measured["silence_sample_fraction"], expected_silence)
                self.assertAlmostEqual(measured["clipping_sample_fraction"], expected_clip)
                if name == "silent":
                    self.assertTrue(measured["digital_silence"])
                    self.assertIsNone(measured["rms_dbfs"])
                else:
                    self.assertAlmostEqual(measured["rms_dbfs"], -3.01, places=2)
                    self.assertEqual(measured["peak_amplitude"], 1)

    def test_hdr_preview_limitation_disclosed(self):
        source = self.base / "hdr.mp4"
        self.ffmpeg("-f", "lavfi", "-i", "color=white:s=80x48:r=10:d=1", "-c:v", "libx264",
                    "-color_trc", "smpte2084", "-colorspace", "bt2020nc", "-color_primaries", "bt2020", source)
        result = self.extractor.extract(source, self.output)
        self.assertTrue(result["derivatives"]["hdr_detected"])
        self.assert_artifacts(result)

    def test_scene_limit_fails_without_partial_success(self):
        extractor = VideoTechnicalExtractor(max_scenes=1)
        with self.assertRaisesRegex(ValueError, "max_scenes=1"):
            extractor.extract(self.cut(), self.output)
        self.assertFalse(self.output.exists())

    def test_failure_cleans_only_own_partial_artifacts(self):
        self.output.mkdir()
        existing = self.output / "keep.jpg"
        existing.write_bytes(b"unrelated existing artifact")
        with patch.object(self.extractor, "_audio", side_effect=ValueError("audio failed")):
            with self.assertRaisesRegex(ValueError, "audio failed"):
                self.extractor.extract(self.static(), self.output)
        self.assertEqual(list(self.output.iterdir()), [existing])

    def test_corrupt_missing_and_audio_only_sources_rejected(self):
        corrupt = self.base / "corrupt.mp4"
        corrupt.write_bytes(b"not a movie")
        with self.assertRaises(ValueError):
            self.extractor.extract(corrupt, self.output)
        with self.assertRaises(FileNotFoundError):
            self.extractor.extract(self.base / "missing.mp4", self.output)
        audio = self.base / "audio.wav"
        self.ffmpeg("-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", "0.2", audio)
        with self.assertRaisesRegex(ValueError, "no video"):
            self.extractor.extract(audio, self.output)
        self.assertFalse(self.output.exists())

    def test_safe_source_path_and_network_protocol_rejection(self):
        source = self.static(name="-quote' ; $(touch nope) video.mp4")
        self.assert_artifacts(self.extractor.extract(source, self.output))
        playlist = self.base / "remote.m3u8"
        playlist.write_text("#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:1,\nhttp://127.0.0.1:1/secret.ts\n#EXT-X-ENDLIST\n")
        with self.assertRaises(ValueError) as caught:
            self.extractor.extract(playlist, self.output)
        self.assertIn("whitelist", str(caught.exception).lower())

    def test_cancellation_kills_real_child_before_joining_readers(self):
        original_wait = subprocess.Popen.wait
        processes = []

        def interrupt_once(process, timeout=None):
            processes.append(process)
            if len(processes) == 1:
                raise KeyboardInterrupt
            return original_wait(process, timeout=timeout)

        with patch("storylens.video_analysis.subprocess.Popen.wait", interrupt_once):
            with self.assertRaises(KeyboardInterrupt):
                self.extractor._run(sys.executable, ["-c", "import time; time.sleep(3)"])
        self.assertIsNotNone(processes[0].poll())

    def test_subprocess_timeout_and_payload_limit(self):
        extractor = VideoTechnicalExtractor(timeout=0.05)
        with self.assertRaisesRegex(ValueError, "timeout"):
            extractor._run(sys.executable, ["-c", "import time; time.sleep(3)"])
        with self.assertRaisesRegex(ValueError, "payload limit"):
            self.extractor._run(sys.executable, ["-c", "print('x' * 10000)"], max_output=100)

    def test_cache_key_includes_tool_versions_threshold_and_scene_limit(self):
        key = self.extractor.cache_key()
        self.assertNotEqual(key, VideoTechnicalExtractor(scene_threshold=0.4).cache_key())
        self.assertNotEqual(key, VideoTechnicalExtractor(max_scenes=2).cache_key())
        self.assertIn("ffmpeg", self.extractor.provenance()["tools"])
        self.assertIn("ffprobe", self.extractor.provenance()["tools"])

    def test_constructor_rejects_invalid_resource_parameters(self):
        for options in ({"timeout": 0}, {"timeout": float("inf")}, {"scene_threshold": 0},
                        {"minimum_scene_seconds": -1}, {"max_scenes": 0}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                VideoTechnicalExtractor(**options)


if __name__ == "__main__":
    unittest.main()
