"""Deterministic transcript normalize/load/serialize behaviors and catalog integration."""

import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from storylens.config import Config
from storylens.service import Analyzer
from storylens.transcripts import load_transcript, normalize, serialize


class TranscriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.media = self.base / "media"
        self.media.mkdir()
        self.config = Config(self.base / "catalog.sqlite3")

    def image(self, name="photo.jpg"):
        path = self.media / name
        Image.new("RGB", (32, 24), "red").save(path)
        return path

    def write(self, name, content):
        path = self.base / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_normalize_preserved_segments_and_words(self):
        document = {
            "language": "en",
            "segments": [
                {"start": 0, "end": 1, "text": "Hello world",
                 "words": [
                     {"start": 0, "end": 0.5, "text": "Hello"},
                     {"start": 0.5, "end": 1, "text": "world"}]}
            ]
        }
        result = normalize(document)
        self.assertEqual(result["language"], "en")
        self.assertEqual(len(result["segments"]), 1)
        segment = result["segments"][0]
        self.assertEqual(segment["index"], 0)
        self.assertEqual(segment["start_seconds"], 0)
        self.assertEqual(segment["end_seconds"], 1)
        self.assertEqual(segment["text"], "Hello world")
        self.assertEqual(len(segment["words"]), 2)
        self.assertEqual(segment["words"][1]["text"], "world")

    def test_normalize_rejects_unordered_segments(self):
        document = {
            "segments": [
                {"start": 2, "end": 3, "text": "second"},
                {"start": 0, "end": 1, "text": "first"}
            ]
        }
        with self.assertRaisesRegex(ValueError, "ordered"):
            normalize(document)

    def test_normalize_rejects_word_outside_segment(self):
        document = {
            "segments": [
                {"start": 0, "end": 1, "text": "only", "words": [
                    {"start": 1.5, "end": 2, "text": "outside"}]}
            ]
        }
        with self.assertRaisesRegex(ValueError, "contained"):
            normalize(document)

    def test_load_json_applies_offset_and_language(self):
        path = self.write("words.json", json.dumps({
            "segments": [
                {"start": 1, "end": 2, "text": "shifted"}
            ]
        }))
        result = load_transcript(path, offset=10, language="es")
        self.assertEqual(result["language"], "es")
        self.assertEqual(result["segments"][0]["start_seconds"], 11)
        self.assertEqual(result["segments"][0]["end_seconds"], 12)
        self.assertEqual(result["provenance"]["format"], "json")
        self.assertEqual(result["provenance"]["offset_seconds"], 10)
        self.assertIn("source_sha256", result["provenance"])

    def test_load_srt(self):
        content = "1\n00:00:01,000 --> 00:00:02,500\nHello\n\n2\n00:00:03,000 --> 00:00:04,000\nWorld\n"
        path = self.write("captions.srt", content)
        result = load_transcript(path)
        self.assertEqual(result["provenance"]["format"], "srt")
        self.assertEqual(len(result["segments"]), 2)
        self.assertEqual(result["segments"][1]["text"], "World")
        self.assertEqual(result["segments"][1]["start_seconds"], 3)

    def test_load_vtt(self):
        content = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nFirst cue\n\nNOTE ignore\n\n00:00:03.000 --> 00:00:04.000\nSecond cue\n"
        path = self.write("captions.vtt", content)
        result = load_transcript(path)
        self.assertEqual(result["provenance"]["format"], "vtt")
        self.assertEqual(len(result["segments"]), 2)
        self.assertEqual(result["segments"][0]["text"], "First cue")

    def test_serialize_json(self):
        result = normalize({
            "segments": [{"start": 0, "end": 1, "text": "hi"}]
        }, language="en")
        text = serialize(result, "json")
        parsed = json.loads(text)
        self.assertEqual(parsed["language"], "en")
        self.assertEqual(parsed["segments"][0]["text"], "hi")

    def test_serialize_srt_roundtrip(self):
        result = load_transcript(self.write("rt.srt", "1\n00:00:01,000 --> 00:00:02,000\nHello\n"))
        srt = serialize(result, "srt")
        self.assertIn("00:00:01,000 --> 00:00:02,000", srt)
        self.assertIn("Hello", srt)
        reparsed = load_transcript(self.write("rt2.srt", srt), format="srt")
        self.assertEqual(reparsed["segments"][0]["text"], "Hello")

    def test_serialize_vtt_roundtrip(self):
        result = load_transcript(self.write("rt.vtt", "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello\n"))
        vtt = serialize(result, "vtt")
        self.assertTrue(vtt.startswith("WEBVTT"))
        reparsed = load_transcript(self.write("rt2.vtt", vtt), format="vtt")
        self.assertEqual(reparsed["segments"][0]["text"], "Hello")

    def test_serialize_subtitle_rejects_negative_time(self):
        result = normalize({"segments": [{"start": -1, "end": 0, "text": "before"}]})
        with self.assertRaisesRegex(ValueError, "negative"):
            serialize(result, "srt")

    def test_service_import_transcript_creates_stage_and_table_row(self):
        source = self.image()
        with Analyzer(self.config) as app:
            app.scan(self.media, analyze=True, stage="metadata")
            media_id = app.catalog.known_path(source)["media_id"]
            transcript_path = self.write("import.json", json.dumps({
                "language": "en",
                "segments": [{"start": 0, "end": 1, "text": "catalogued"}]
            }))
            result = app.import_transcript(media_id, transcript_path, language="en")
            stored = app.catalog.transcript(media_id)
            analysis = app.catalog.get(media_id)["analysis"]["transcript"]
        self.assertEqual(result["segments"][0]["text"], "catalogued")
        self.assertEqual(stored["language"], "en")
        self.assertEqual(analysis["result"]["segments"][0]["text"], "catalogued")


if __name__ == "__main__":
    unittest.main()
