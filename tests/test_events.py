"""Deterministic grouping behavior, including generated-media metadata smoke tests."""

from copy import deepcopy
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

from PIL import Image

from storylens.events import DEFAULT_SETTINGS, group_media, normalize_settings
from storylens.metadata import MetadataExtractor


def media(identifier, capture="2025:06:01 12:00:00", *, offset=None, gps=None, vector=None,
          space="space-a", depth="standard", algorithm="semantics-v1", provider=None):
    tags = {} if capture is None else {"DateTimeOriginal": capture}
    if offset is not None:
        tags["OffsetTimeOriginal"] = offset
    metadata = {"exif": {"ExifIFD": tags}}
    if gps is not None:
        metadata["gps"] = gps
    item = {"id": identifier, "analysis": {"metadata": {
        "cache_key": "metadata-current", "analyzed_at": "2025-06-02T00:00:00+00:00", "result": metadata}}}
    if vector is not None:
        item["analysis"]["semantic_embeddings"] = {
            "cache_key": "embedding-current", "analyzed_at": "2025-06-02T00:00:00+00:00",
            "result": {"provenance": {"space": space, "depth": depth, "algorithm": algorithm,
                                        "provider": provider or {"model": "local-test", "revision": "1"}},
                       "items": [{"vector": vector, "evidence": {"kind": "image"}}]}}
    return item


def memberships(result):
    return [tuple(member["media_id"] for member in event["members"]) for event in result["events"]]


class EventSettingsTests(unittest.TestCase):
    def test_unknown_nonfinite_boolean_and_nonsensical_settings_fail(self):
        for values in ({"unknown": 1}, {"gap_seconds": True}, {"distance_km": 0},
                       {"distance_km": -1}, {"gap_seconds": float("nan")},
                       {"semantic_similarity": float("inf")}, {"semantic_similarity": -0.1},
                       {"semantic_similarity": 1.1}, {"semantic_similarity": False},
                       {"semantic_depth": "automatic"}, {"gap_seconds": 21601},
                       {"semantic_gap_seconds": 43201}, {"max_event_seconds": 1},
                       {"gap_seconds": 604801}, {"distance_km": 20041},
                       {"max_event_seconds": 31536001}, {"gap_seconds": "7200"},
                       {"gap_seconds": 10 ** 1000}, {"semantic_similarity": 10 ** 1000}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                normalize_settings(values)
        for values in ([], "standard", False):
            with self.assertRaises(ValueError):
                normalize_settings(values)


class EventGroupingTests(unittest.TestCase):
    def test_burst_is_not_fragmented_by_scenes_or_visual_dissimilarity(self):
        items = []
        for index in range(100):
            capture = (datetime(2025, 6, 1, 12) + timedelta(seconds=index * 30)).strftime("%Y:%m:%d %H:%M:%S")
            item = media(str(index), capture, vector=[1, 0] if index % 2 else [-1, 0])
            item["analysis"]["video_technical"] = {"result": {"scenes": [{"index": i} for i in range(20)]}}
            items.append(item)
        result = group_media(items)
        self.assertEqual(len(result["events"]), 1)
        self.assertEqual(result["events"][0]["member_count"], 100)
        self.assertEqual(result["events"][0]["members"][1]["evidence"]["join"]["decision"], "close_time")
        self.assertEqual(result["events"][0]["members"][1]["evidence"]["join"]["cosine"], -1)

    def test_day_gap_separates_even_identical_embeddings(self):
        result = group_media([media("a", vector=[1, 0]),
                              media("b", "2025:06:02 12:00:00", vector=[1, 0])])
        self.assertEqual(memberships(result), [("a",), ("b",)])
        self.assertEqual(result["events"][1]["members"][0]["evidence"]["join"]["decision"], "span_limit")

    def test_gap_threshold_inclusive_and_group_span_bound(self):
        items = [media(str(i), f"2025:06:01 {hour:02}:00:00") for i, hour in enumerate((0, 2, 4, 6, 8, 10, 12, 14))]
        self.assertEqual([event["member_count"] for event in group_media(items)["events"]], [7, 1])
        separated = group_media([media("a"), media("b", "2025:06:01 14:00:01")])
        self.assertEqual(len(separated["events"]), 2)

    def test_gps_veto_and_missing_gps_permissive(self):
        near = {"latitude": 0, "longitude": 0}
        far = {"latitude": 0, "longitude": 2}
        items = [media("a", gps=near), media("b", "2025:06:01 12:01:00", gps=far)]
        result = group_media(items)
        self.assertEqual(memberships(result), [("a",), ("b",)])
        join = result["events"][1]["members"][0]["evidence"]["join"]
        self.assertTrue(join["gps_veto"])
        self.assertGreater(join["distance_km"], 200)
        items[1]["analysis"]["metadata"]["result"].pop("gps")
        self.assertEqual(memberships(group_media(items)), [("a", "b")])
        items.insert(1, media("between", "2025:06:01 12:00:30"))
        items[-1]["analysis"]["metadata"]["result"]["gps"] = far
        self.assertEqual(memberships(group_media(items)), [("a", "between"), ("b",)])

    def test_semantic_bridge_requires_compatible_space_depth_algorithm_model_dimension(self):
        first = media("a", vector=[1, 0])
        second = media("b", "2025:06:01 15:00:00", vector=[1, 0])
        result = group_media([first, second])
        self.assertEqual(memberships(result), [("a", "b")])
        join = result["events"][0]["members"][1]["evidence"]["join"]
        self.assertEqual(join["decision"], "semantic_bridge")
        self.assertEqual(join["cosine"], 1)
        self.assertTrue(result["events"][0]["warnings"])
        for changes in ({"space": "other"}, {"depth": "deep"}, {"algorithm": "different"},
                        {"provider": {"model": "other"}}, {"vector": [1, 0, 0]}, {"vector": [0, 1]}):
            with self.subTest(changes=changes):
                incompatible = media("b", "2025:06:01 15:00:00", **{"vector": [1, 0], **changes})
                self.assertEqual(len(group_media([first, incompatible])["events"]), 2)
        second["analysis"]["metadata"]["result"]["gps"] = {"latitude": 0, "longitude": 3}
        first["analysis"]["metadata"]["result"]["gps"] = {"latitude": 0, "longitude": 0}
        self.assertEqual(len(group_media([first, second])["events"]), 2)

    def test_bad_or_non_normalized_embeddings_are_not_evidence(self):
        first = media("a", vector=[1, 0])
        for vector in ([2, 0], [0, 0], [float("nan"), 0], [True, 0], [], ["1", 0]):
            with self.subTest(vector=vector):
                result = group_media([first, media("b", "2025:06:01 15:00:00", vector=vector)])
                self.assertEqual(len(result["events"]), 2)
                self.assertIsNone(result["events"][1]["members"][0]["evidence"]["embedding"])
                json.dumps(result, allow_nan=False)

    def test_relevant_latest_stage_and_normalized_frame_mean(self):
        first = media("a", vector=[1, 0])
        second = media("b", "2025:06:01 15:00:00", vector=[1, 0])
        stage = second["analysis"]["semantic_embeddings"]
        stage["result"]["items"].append({"vector": [1, 0], "evidence": {"kind": "video_frame"}})
        old = deepcopy(stage)
        old["analyzed_at"] = "2024-01-01"
        old["result"]["items"][0]["vector"] = [-1, 0]
        other_depth = deepcopy(stage)
        other_depth["analyzed_at"] = "2026-01-01"
        other_depth["result"]["provenance"]["depth"] = "deep"
        second["analysis"]["semantic_embeddings"] = [other_depth, old, stage]
        result = group_media([first, second])
        self.assertEqual(memberships(result), [("a", "b")])
        self.assertEqual(result["events"][0]["members"][1]["evidence"]["embedding"]["vector_count"], 2)

    def test_offsets_compare_utc_and_naive_times_never_mix(self):
        items = [media("utc", "2025:06:01 10:00:00", offset="+00:00"),
                 media("local", "2025:06:01 12:01:00", offset="+02:00"),
                 media("naive", "2025:06:01 10:00:30")]
        result = group_media(items)
        self.assertEqual(memberships(result), [("utc", "local"), ("naive",)])
        self.assertEqual(result["events"][0]["timezone_status"], "explicit_offset")
        self.assertEqual(result["events"][0]["start_time"], "2025-06-01T10:00:00+00:00")
        self.assertEqual(result["events"][1]["timezone_status"], "timezone_ambiguous")
        self.assertEqual(result["events"][1]["start_time"], "2025-06-01T10:00:30")
        self.assertTrue(result["events"][1]["warnings"])

    @unittest.skipUnless(hasattr(time, "tzset"), "OS timezone switching requires tzset")
    def test_os_timezone_independence(self):
        previous = os.environ.get("TZ")
        self.addCleanup(self.restore_timezone, previous)
        items = [media("a"), media("b", "2025:06:01 12:02:00"), media("c", offset="-05:00")]
        results = []
        for zone in ("UTC0", "EST5EDT", "JST-9"):
            os.environ["TZ"] = zone
            time.tzset()
            results.append(group_media(items))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[1], results[2])

    @staticmethod
    def restore_timezone(previous):
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()

    def test_timestamp_precedence_and_ffprobe_stream_index_order(self):
        item = media("a", "2025:06:01 12:00:00", offset="+02:00")
        metadata = item["analysis"]["metadata"]["result"]
        metadata.update({"exif": {**metadata["exif"], "DateTimeOriginal": "2020:01:01 00:00:00"},
                         "tags": {"creation_time": "2019-01-01T00:00:00Z"},
                         "streams": [{"index": 3, "tags": {"creation_time": "2018-01-01T00:00:00Z"}},
                                     {"index": 1, "tags": {"creation_time": "2017-01-01T00:00:00Z"}}]})
        self.assertEqual(group_media([item])["events"][0]["start_time"], "2025-06-01T10:00:00+00:00")
        metadata["exif"].pop("ExifIFD")
        self.assertEqual(group_media([item])["events"][0]["start_time"], "2020-01-01T00:00:00")
        metadata.pop("exif")
        self.assertEqual(group_media([item])["events"][0]["start_time"], "2019-01-01T00:00:00+00:00")
        metadata.pop("tags")
        result = group_media([item])
        self.assertEqual(result["events"][0]["start_time"], "2017-01-01T00:00:00+00:00")
        self.assertIn("streams[1]", result["events"][0]["members"][0]["evidence"]["capture_time"]["source"])

    def test_invalid_missing_or_explicit_unknown_time_is_review_bucket_not_event(self):
        items = [media("missing", None), media("invalid", "not-a-time"), media("unknown")]
        items[-1]["feedback"] = {"capture_time": None}
        items[0]["paths"] = [{"mtime_ns": 1748779200000000000}]
        result = group_media(items)
        self.assertEqual(memberships(result), [("invalid", "missing", "unknown")])
        event = result["events"][0]
        self.assertEqual(event["kind"], "undated")
        self.assertEqual(event["timezone_status"], "unresolved")
        self.assertIsNone(event["start_time"])
        self.assertIn("no factual event", " ".join(event["warnings"]))
        evidence = event["members"][-1]["evidence"]["capture_time"]
        self.assertTrue(evidence["explicit_unknown"])
        self.assertEqual(evidence["original"]["value"], "2025-06-01T12:00:00")

    def test_manual_priority_corrections_selection_and_segments_are_not_rewritten(self):
        items = [media("a", gps={"latitude": 0, "longitude": 0}),
                 media("b", "2025:06:05 12:00:00", gps={"latitude": 0, "longitude": 50}), media("c")]
        items[0]["feedback"] = {"event_id": "manual-trip", "selection": "exclude", "gps": None,
                                "capture_time": None, "segments": [{"start_seconds": 1, "end_seconds": 2}]}
        items[1]["feedback"] = {"event_id": "manual-trip", "selection": "include"}
        before = deepcopy(items)
        registry = [{"id": "manual-trip", "title": "My trip"}, {"id": "empty", "title": "PRIVATE EMPTY TITLE"}]
        result = group_media(items, manual_events=registry)
        event = result["events"][0]
        self.assertEqual(event["id"], "manual-trip")
        self.assertEqual(event["title"], "My trip")
        self.assertEqual(event["title_provenance"]["kind"], "user")
        self.assertEqual(event["kind"], "manual")
        self.assertEqual(event["member_count"], 2)
        self.assertEqual(event["members"][0]["selection"], "exclude")
        self.assertIsNone(event["members"][0]["evidence"]["gps"]["value"])
        self.assertEqual(event["members"][0]["evidence"]["gps"]["original"]["value"]["longitude"], 0)
        self.assertEqual(event["members"][0]["evidence"]["segments"]["source"], "feedback.segments")
        self.assertNotIn("PRIVATE EMPTY TITLE", json.dumps(result))
        self.assertEqual(items, before)
        result["events"][0]["members"][0]["evidence"]["segments"]["values"][0]["start_seconds"] = 99
        self.assertEqual(items, before)
        items[0]["feedback"]["event_id"] = None
        result = group_media(items, manual_events=registry)
        self.assertEqual(result["events"][0]["member_count"], 1)

    def test_override_restores_time_and_gps_without_replacing_generated_evidence(self):
        item = media("a", None)
        item["feedback"] = {"capture_time": "2025-01-02T03:04:05-05:00",
                            "gps": {"latitude": 1, "longitude": 2}}
        event = group_media([item])["events"][0]
        self.assertEqual(event["start_time"], "2025-01-02T08:04:05+00:00")
        self.assertEqual(event["members"][0]["evidence"]["gps"]["value"], {"latitude": 1, "longitude": 2})
        self.assertEqual(event["title"], "2025-01-02")
        self.assertEqual(event["title_provenance"]["kind"], "metadata_date")

    def test_determinism_stable_membership_ids_and_private_payload_omission(self):
        items = [media("b", "2025:06:01 12:01:00", vector=[1, 0]), media("a", vector=[1, 0])]
        items[0]["paths"] = [{"path": "/private/source.jpg", "mtime_ns": 1}]
        items[0]["analysis"]["metadata"]["result"]["raw_private"] = "private raw metadata"
        result = group_media(iter(items))
        self.assertEqual(result, group_media(reversed(items)))
        self.assertEqual(result["schema_version"], "1.0")
        self.assertEqual(result["provenance"]["algorithm"], "events-1")
        self.assertEqual(len(result["provenance"]["input_digest"]), 64)
        encoded = json.dumps(result, allow_nan=False)
        self.assertNotIn("/private/source.jpg", encoded)
        self.assertNotIn("private raw metadata", encoded)
        self.assertNotIn('"vector":', encoded)
        original_id = result["events"][0]["id"]
        items[0]["feedback"] = {"selection": "exclude"}
        changed = group_media(items)
        self.assertEqual(changed["events"][0]["id"], original_id)
        self.assertNotEqual(changed["provenance"]["input_digest"], result["provenance"]["input_digest"])

    def test_minute_precision_corrections_drive_grouping_and_title_provenance(self):
        item = media("a")
        item["feedback"] = {"capture_time": "2026-10-16T10:20"}
        event = group_media([item])["events"][0]
        self.assertEqual(event["start_time"], "2026-10-16T10:20:00")
        self.assertEqual(event["title"], "2026-10-16")
        self.assertEqual(event["title_provenance"]["sources"], [
            {"media_id": "a", "source": "feedback.capture_time", "cache_key": None}])

    def test_limits_and_invalid_ids_fail_without_silent_truncation(self):
        result = group_media(media(str(index), None) for index in range(10000))
        self.assertEqual(result["events"][0]["member_count"], 10000)
        self.assertEqual(len(result["events"][0]["members"]), 10000)
        with self.assertRaisesRegex(ValueError, "10000"):
            group_media(media(str(index), None) for index in range(10001))
        for items in ([media("a"), media("a")], [media("")]):
            with self.assertRaises(ValueError):
                group_media(items)
        item = media("a")
        item["feedback"] = {"event_id": "missing"}
        with self.assertRaisesRegex(ValueError, "Unknown manual event"):
            group_media([item])
        self.assertEqual(group_media([])["events"], [])


class GeneratedMediaEventTests(unittest.TestCase):
    def test_real_jpeg_exif_burst_groups_read_only_without_models(self):
        with tempfile.TemporaryDirectory() as directory:
            items = []
            originals = {}
            extractor = MetadataExtractor()
            for index in range(4):
                path = Path(directory) / f"shot-{index}.jpg"
                exif = Image.Exif()
                exif[34665] = {36867: f"2025:07:08 12:0{index}:00", 36881: "+02:00"}
                Image.new("RGB", (32, 24), (index * 60, 0, 255 - index * 60)).save(path, exif=exif)
                originals[path] = path.read_bytes()
                items.append({"id": str(index), "analysis": {"metadata": {
                    "cache_key": extractor.cache_key("image"), "result": extractor.extract(path, "image")}}})
            result = group_media(items)
            self.assertEqual(memberships(result), [("0", "1", "2", "3")])
            self.assertEqual(result["events"][0]["start_time"], "2025-07-08T10:00:00+00:00")
            self.assertEqual(result["events"][0]["title"], "2025-07-08")
            for path, original in originals.items():
                self.assertEqual(path.read_bytes(), original)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg/ffprobe unavailable")
    def test_real_mp4_creation_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "clip.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=32x24:d=0.1",
                            "-c:v", "mpeg4", "-metadata", "creation_time=2025-08-09T10:11:12Z", str(path)],
                           check=True, capture_output=True, timeout=30)
            before = path.read_bytes()
            extractor = MetadataExtractor()
            item = {"id": "clip", "analysis": {"metadata": {"cache_key": extractor.cache_key("video"),
                                                              "result": extractor.extract(path, "video")}}}
            event = group_media([item])["events"][0]
            self.assertEqual(event["start_time"], "2025-08-09T10:11:12+00:00")
            self.assertEqual(event["timezone_status"], "explicit_offset")
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
