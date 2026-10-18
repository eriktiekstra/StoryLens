"""Consumer-visible editor identity, source-time and approval boundaries."""

from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import wave

from storylens.access import PathPolicy
from storylens.config import Config
from storylens.filmcraft import (
    TICKS, _approve, _destination, _plan, _saved_sources, _source,
    _verify_origin, _verify_sequence, import_transcript,
)
from storylens.service import Analyzer


class EditorTimingTests(unittest.TestCase):
    def test_fractional_frame_rate_keeps_integer_ticks_without_cumulative_drift(self):
        document = {"duration_seconds": 5, "items": [
            {"kind": "video", "duration_seconds": 1.001, "start_seconds": 5.5},
            {"kind": "image", "duration_seconds": 1.001},
            {"kind": "video", "duration_seconds": 1.001, "start_seconds": 6.501},
        ]}
        plan = _plan(document, Fraction(24000, 1001))
        self.assertEqual([row["time"] for row in plan], [0, 254270016000, 508540032000])
        self.assertEqual(plan[0]["sourceIn"], 1397088000000)
        self.assertEqual(plan[-1]["time"] + plan[-1]["duration"], 762810048000)

    def test_off_grid_or_collapsed_entries_fail_before_editor_can_snap(self):
        for duration in (1.01, 0.001):
            with self.subTest(duration=duration), self.assertRaisesRegex(ValueError, "frame grid"):
                _plan({"duration_seconds": 5, "items": [{"kind": "image", "duration_seconds": duration}]},
                      Fraction(24))

    def test_nonzero_origin_is_not_silently_rebased(self):
        media = {"kind": "video", "analysis": {"video_technical": {"result": {
            "source_timing": {"origin_seconds": 5, "end_seconds": 8}}}}}
        _verify_origin(media, Path("source.mp4"), 8 * TICKS)
        for path, duration in ((Path("source.mp4"), 3 * TICKS), (Path("source.mkv"), 8 * TICKS)):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "origin mapping"):
                _verify_origin(media, path, duration)

    def test_post_placement_inspection_rejects_unapproved_timing_and_extra_clips(self):
        row = {"time": 0, "duration": TICKS, "sourceIn": 5 * TICKS}
        clip = {"clip": 9, "item": 3, "start": 0, "end": TICKS, "sourceIn": 5 * TICKS,
                "sourceOut": 6 * TICKS, "speed": 1, "reverse": False, "enabled": True}
        state = {"settings": {"frame_rate": {"num": 24, "den": 1}}, "id": 7,
                 "video": [{"items": [clip]}], "audio": [], "duration": TICKS}
        expected = [(row, 3, [9])]
        _verify_sequence(state, Fraction(24), 7, expected)
        for key, value in (("sourceIn", 0), ("end", TICKS + 1), ("reverse", True)):
            changed = deepcopy(state)
            changed["video"][0]["items"][0][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "timing, identity or playback"):
                _verify_sequence(changed, Fraction(24), 7, expected)
        changed = deepcopy(state)
        changed["video"][0]["items"].append({**clip, "clip": 10})
        with self.assertRaisesRegex(ValueError, "inventory"):
            _verify_sequence(changed, Fraction(24), 7, expected)


class EditorSourceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.media_root = self.root / "media"
        self.media_root.mkdir()
        self.source = self.media_root / "tone.wav"
        with wave.open(str(self.source), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(8000)
            output.writeframes(b"\x00\x00" * 16000)
        self.media_id = "sha256:" + hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.media = {"id": self.media_id, "kind": "audio", "paths": [{"path": str(self.source)}]}
        self.policy = PathPolicy([self.media_root])
        self.project_path = self.root / "source.fcproj"
        self.project = {"format": "filmcraft.project", "schema_version": 15,
                        "project": {"items": {"4": {"kind": {"Media": {
                            "media": {"File": {"path": str(self.source)}}, "info": {"duration": 2 * TICKS}}}}},
                            "transcripts": {"4": {"language": "en", "speakers": [{"name": "Alice"}], "words": [
                                {"text": "Hello", "start": 0, "end": TICKS // 2, "speaker": 0},
                                {"text": "world", "start": TICKS // 2, "end": TICKS, "speaker": 0}]}}}}
        self.config = Config(self.root / "catalog.sqlite3")
        with Analyzer(self.config) as app:
            app.scan(self.media_root)
            app.catalog.save(self.media_id, "audio_technical", "fixture", {
                "source_timing": {"origin_seconds": 0, "end_seconds": 2}})

    def test_changed_source_bytes_cannot_reuse_catalog_identity(self):
        self.assertEqual(_source(self.media, self.policy), self.source)
        self.source.write_bytes(b"changed bytes")
        with self.assertRaisesRegex(ValueError, "unchanged source"):
            _source(self.media, self.policy)

    def test_approval_and_output_boundaries_prevent_source_writes_and_overwrite(self):
        with self.assertRaisesRegex(ValueError, "explicit --approve"):
            _approve(False)
        with self.assertRaisesRegex(ValueError, "source roots"):
            _destination(self.media_root / "movie.mp4", self.policy)
        existing = self.root / "existing.mp4"
        existing.write_bytes(b"keep")
        with self.assertRaisesRegex(ValueError, "already exists"):
            _destination(existing, self.policy)
        self.assertEqual(existing.read_bytes(), b"keep")

    def test_private_proxy_and_future_schema_are_rejected(self):
        for change in ("proxy", "private", "schema"):
            project = deepcopy(self.project)
            media = project["project"]["items"]["4"]["kind"]["Media"]
            if change == "proxy":
                media["proxy"] = {"path": str(self.root / "secret.wav")}
            elif change == "private":
                secret = self.root / "secret.wav"
                secret.write_bytes(b"secret")
                media["media"]["File"]["path"] = str(secret)
            else:
                project["schema_version"] = 16
            with self.subTest(change=change), self.assertRaises(ValueError):
                _saved_sources(project, self.policy)

    def test_saved_source_transcript_preserves_words_speaker_and_absolute_times(self):
        self.project_path.write_text(json.dumps(self.project))
        with Analyzer(self.config) as app:
            result = import_transcript(app, project=self.project_path, item_id=4,
                                       media_id=self.media_id, roots=[self.media_root])
            segment = result["segments"][0]
            self.assertEqual((segment["text"], segment["speaker"], segment["start_seconds"], segment["end_seconds"]),
                             ("Hello world", "Alice", 0, 1))
            self.assertEqual([(w["text"], w["start_seconds"], w["end_seconds"]) for w in segment["words"]],
                             [("Hello", 0, 0.5), ("world", 0.5, 1)])
            self.assertEqual(app.transcript(self.media_id), result)

    def test_wrong_item_or_out_of_source_word_cannot_attach_transcript(self):
        self.project_path.write_text(json.dumps(self.project))
        with Analyzer(self.config) as app:
            with self.assertRaisesRegex(ValueError, "does not map"):
                import_transcript(app, project=self.project_path, item_id=5,
                                  media_id=self.media_id, roots=[self.media_root])
            self.project["project"]["transcripts"]["4"]["words"][1]["end"] = 3 * TICKS
            self.project_path.write_text(json.dumps(self.project))
            with self.assertRaisesRegex(ValueError, "outside source coverage"):
                import_transcript(app, project=self.project_path, item_id=4,
                                  media_id=self.media_id, roots=[self.media_root])
            with self.assertRaisesRegex(ValueError, "No transcript"):
                app.transcript(self.media_id)
