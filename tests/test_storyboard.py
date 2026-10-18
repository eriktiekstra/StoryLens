"""Portable storyboard edits are checked against stored evidence and user constraints."""

from copy import deepcopy
import json
import math
import unittest

from storylens.storyboard import conversation_blocks, validate_constraints, validate_storyboard


def timed_media(media_id="audio", kind="audio", start=-5, end=30, **extra):
    return {"id": media_id, "kind": kind, "analysis": {
        kind + "_technical": {"result": {"source_timing": {
            "origin_seconds": start, "end_seconds": end}}}}, **extra}


def entry(media_id="image", kind="image", start=None, end=None, duration=4, required=False):
    return {"media_id": media_id, "kind": kind, "start_seconds": start, "end_seconds": end,
            "duration_seconds": duration if kind == "image" else end - start,
            "required": required, "scores": {"technical": None, "relevance": None,
                                             "story": 0.5, "total": 0.5},
            "reason": "Timing and stored evidence only"}


def document(items=None, *, required=None, excluded=None, budget=60):
    items = [entry()] if items is None else items
    return {"schema_version": "1.0", "kind": "storylens_storyboard", "title": "My story",
            "duration_seconds": budget, "total_seconds": math.fsum(item["duration_seconds"] for item in items),
            "strategy": "balanced", "weights": {"technical": 0.4, "relevance": 0.3, "story": 0.3},
            "required": [] if required is None else required,
            "excluded": [] if excluded is None else excluded, "items": items, "warnings": [],
            "provenance": {"algorithm": "recommendations-1", "caveats": ["Not semantic comprehension"]}}


def segment(start, end, selection="include"):
    return {"start_seconds": start, "end_seconds": end, "selection": selection}


class StoryboardTests(unittest.TestCase):
    def setUp(self):
        self.media = {"image": {"id": "image", "kind": "image", "analysis": {}},
                      "audio": timed_media(),
                      "video": timed_media("video", "video", 7, 27)}

    def test_portable_json_editing_order_title_holds_and_source_intervals(self):
        original = document([entry(), entry("audio", "audio", -3, 2)])
        edited = json.loads(json.dumps(original, allow_nan=False))
        edited["title"] = "A different title"
        edited["items"].reverse()
        edited["items"][0].update(start_seconds=-4, end_seconds=3, duration_seconds=7)
        edited["items"][1]["duration_seconds"] = 8
        edited["total_seconds"] = 15
        before_document, before_media = deepcopy(edited), deepcopy(self.media)
        validate_storyboard(edited, self.media)
        self.assertEqual(edited, before_document)
        self.assertEqual(self.media, before_media)
        self.assertEqual(original["title"], "My story")
        self.assertEqual(original["items"][0]["duration_seconds"], 4)

    def test_recommended_document_rejects_invalid_hold_edits(self):
        from storylens.recommendations import recommend

        result = json.loads(json.dumps(recommend([self.media["image"]]), allow_nan=False))
        result["items"][0]["duration_seconds"] = result["duration_seconds"] + 1
        result["total_seconds"] = result["items"][0]["duration_seconds"]
        with self.assertRaisesRegex(ValueError, "budget"):
            validate_storyboard(result, self.media)
        result["items"][0]["duration_seconds"] = 0
        result["total_seconds"] = 0
        with self.assertRaisesRegex(ValueError, "positive"):
            validate_storyboard(result, self.media)

    def test_exact_shape_and_nested_types_are_enforced(self):
        mutations = [([], None, None), ([], "path", "/private/source"),
                     ([], "schema_version", "2.0"), ([], "kind", "other"),
                     ([], "title", 1), ([], "strategy", "random"),
                     ([], "warnings", [None]), ([], "required", None),
                     ([], "excluded", "image"), ([], "items", {}),
                     (["weights"], "other", 0), (["weights"], "technical", None),
                     (["provenance"], "algorithm", "other"),
                     (["provenance"], "caveats", "text"),
                     (["provenance"], "private_path", "/secret"),
                     (["items", 0], "path", "/secret"),
                     (["items", 0], "media_id", []),
                     (["items", 0], "kind", "audio"),
                     (["items", 0], "required", 1),
                     (["items", 0], "reason", None),
                     (["items", 0, "scores"], "confidence", 0.5)]
        for path, key, value in mutations:
            candidate = document()
            target = candidate
            for component in path:
                target = target[component]
            if key is None:
                del target["title"]
            else:
                target[key] = value
            with self.subTest(path=path, key=key), self.assertRaises(ValueError):
                validate_storyboard(candidate, self.media)
        for value in (None, [], "storyboard"):
            with self.assertRaises(ValueError):
                validate_storyboard(value, self.media)

    def test_finite_budget_totals_weights_and_scores(self):
        for invalid in (True, False, float("nan"), float("inf"), -float("inf"), 10 ** 1000, "4", None):
            for path in (("duration_seconds",), ("total_seconds",), ("weights", "technical"),
                         ("items", 0, "duration_seconds"), ("items", 0, "scores", "story"),
                         ("items", 0, "scores", "total")):
                candidate = document()
                target = candidate
                for component in path[:-1]:
                    target = target[component]
                target[path[-1]] = invalid
                with self.subTest(value=invalid, path=path), self.assertRaises(ValueError):
                    validate_storyboard(candidate, self.media)
        for name in ("technical", "relevance", "story", "total"):
            for invalid in (-0.01, 1.01, True, float("nan")):
                candidate = document()
                candidate["items"][0]["scores"][name] = invalid
                with self.subTest(score=name, value=invalid), self.assertRaises(ValueError):
                    validate_storyboard(candidate, self.media)
        for weights in ({"technical": 0, "relevance": 0, "story": 0},
                        {"technical": 0.5, "relevance": 0.5, "story": 0.5},
                        {"technical": -1, "relevance": 1, "story": 1}):
            candidate = document()
            candidate["weights"] = weights
            with self.assertRaises(ValueError):
                validate_storyboard(candidate, self.media)
        for budget, total, hold in ((0, 4, 4), (-1, 4, 4), (3, 4, 4), (60, 3, 4), (60, -1, 4), (60, 0, 0)):
            candidate = document(budget=budget)
            candidate["total_seconds"] = total
            candidate["items"][0]["duration_seconds"] = hold
            with self.subTest(budget=budget, total=total, hold=hold), self.assertRaises(ValueError):
                validate_storyboard(candidate, self.media)

    def test_source_coverage_preserves_negative_and_nonzero_origins(self):
        for media_id, kind, start, end in (("audio", "audio", -5.1, 0),
                                            ("audio", "audio", 29, 30.1),
                                            ("video", "video", 0, 5),
                                            ("video", "video", 26, 28),
                                            ("audio", "audio", 1, 1)):
            with self.subTest(media_id=media_id, start=start, end=end), self.assertRaises(ValueError):
                validate_storyboard(document([entry(media_id, kind, start, end)]), self.media)
        for key in ("start_seconds", "end_seconds"):
            for value in (None, True, float("nan"), float("inf")):
                candidate = document([entry("audio", "audio", 0, 3)])
                candidate["items"][0][key] = value
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    validate_storyboard(candidate, self.media)
        candidate = document([entry("audio", "audio", 0, 3)])
        candidate["items"][0]["duration_seconds"] = 2
        candidate["total_seconds"] = 2
        with self.assertRaisesRegex(ValueError, "match.*source interval"):
            validate_storyboard(candidate, self.media)

    def test_metadata_source_coverage_and_unknown_timing(self):
        self.media["video"]["analysis"] = {"metadata": {"result": {
            "duration_seconds": "100", "streams": [{"codec_type": "video", "start_time": "7", "duration": "3"}]}}}
        with self.assertRaises(ValueError):
            validate_storyboard(document([entry("video", "video", 7, 11)]), self.media)
        self.media["audio"]["analysis"] = {}
        with self.assertRaisesRegex(ValueError, "coverage"):
            validate_storyboard(document([entry("audio", "audio", 0, 2)]), self.media)

    def test_no_duplicate_images_or_overlapping_source_intervals(self):
        for items in ([entry(), entry()],
                      [entry("audio", "audio", -1, 3), entry("audio", "audio", 2, 5)],
                      [entry("audio", "audio", 0, 2), entry("audio", "audio", 0, 2)]):
            with self.assertRaises(ValueError):
                validate_storyboard(document(items), self.media)
        candidate = document()
        candidate["items"][0]["start_seconds"] = 0
        with self.assertRaisesRegex(ValueError, "null"):
            validate_storyboard(candidate, self.media)

    def test_required_and_excluded_media_and_flags(self):
        for candidate in (document([], required=["image"]), document(excluded=["image"]),
                          document(required=["image"], excluded=["image"]),
                          document(required=["unknown"]), document(excluded=["unknown"]),
                          document(required=["image", "image"]), document(excluded=["video", "video"]),
                          document(required=["image"]), document([entry(required=True)])):
            with self.assertRaises(ValueError):
                validate_storyboard(candidate, self.media)
        with self.assertRaisesRegex(ValueError, "Unknown"):
            validate_storyboard(document([entry("unknown")]), self.media)

    def test_current_feedback_cannot_be_erased_by_edited_arrays(self):
        self.media["image"]["feedback"] = {"selection": "include"}
        self.media["video"]["feedback"] = {"selection": "exclude"}
        valid = document([entry(required=True)], required=["image"], excluded=["video"])
        for field in ("required", "excluded"):
            candidate = deepcopy(valid)
            candidate[field] = []
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "feedback"):
                validate_storyboard(candidate, self.media)
        with self.assertRaises(ValueError):
            validate_storyboard(document([], excluded=["image", "video"]), self.media)

    def test_required_include_union_is_covered_and_flagged(self):
        self.media["audio"]["feedback"] = {"segments": [segment(0, 2), segment(1, 4), segment(8, 10)]}
        valid = document([entry("audio", "audio", 0, 2, required=True),
                          entry("audio", "audio", 2, 4, required=True),
                          entry("audio", "audio", 8, 10, required=True)])
        missing = deepcopy(valid)
        missing["items"].pop()
        missing["total_seconds"] = 4
        with self.assertRaisesRegex(ValueError, "required include"):
            validate_storyboard(missing, self.media)
        hole = document([entry("audio", "audio", 0, 1.5, required=True),
                         entry("audio", "audio", 2, 4, required=True),
                         entry("audio", "audio", 8, 10, required=True)])
        with self.assertRaises(ValueError):
            validate_storyboard(hole, self.media)
        wrong_flag = deepcopy(valid)
        wrong_flag["items"][0]["required"] = False
        with self.assertRaisesRegex(ValueError, "required flag"):
            validate_storyboard(wrong_flag, self.media)
        with self.assertRaises(ValueError):
            validate_storyboard(document([], excluded=["audio"]), self.media)

    def test_excluded_segments_are_half_open_and_cannot_be_selected(self):
        self.media["audio"]["feedback"] = {"segments": [segment(2, 4, "exclude")]}
        with self.assertRaisesRegex(ValueError, "excluded segment"):
            validate_storyboard(document([entry("audio", "audio", 1, 3)]), self.media)
        self.media["audio"]["feedback"]["segments"].append(segment(3, 5))
        with self.assertRaisesRegex(ValueError, "conflicting"):
            validate_storyboard(document(), self.media)

    def test_atomic_conversation_merges_overlap_and_up_to_two_second_gaps(self):
        self.media["audio"]["transcript"] = {"segments": [segment(0, 3), segment(2, 4),
                                                                   segment(6, 8), segment(10.01, 12)]}
        self.assertEqual(conversation_blocks(self.media["audio"]), [(0, 8), (10.01, 12)])
        for items in ([entry("audio", "audio", 1, 8)], [entry("audio", "audio", 0, 7)],
                      [entry("audio", "audio", 0, 4), entry("audio", "audio", 4, 8)],
                      [entry("audio", "audio", 4, 6)]):
            with self.subTest(items=items), self.assertRaisesRegex(ValueError, "atomic conversation"):
                validate_storyboard(document(items), self.media)
        with self.assertRaisesRegex(ValueError, "budget"):
            validate_storyboard(document([entry("audio", "audio", 0, 8)], budget=7), self.media)

    def test_imported_transcript_precedes_stored_and_inferred_transcripts(self):
        item = self.media["audio"]
        item["analysis"]["audio_transcription"] = {"result": {"segments": [segment(0, 10)]}}
        self.assertEqual(conversation_blocks(item), [(0, 10)])
        item["analysis"]["transcript"] = {"result": {"segments": [segment(1, 7)]}}
        self.assertEqual(conversation_blocks(item), [(1, 7)])
        item["transcript"] = {"segments": [segment(2, 4)]}
        self.assertEqual(conversation_blocks(item), [(2, 4)])
        item["transcript"] = {"segments": []}
        self.assertEqual(conversation_blocks(item), [])

    def test_excluded_span_interrupting_conversation_makes_whole_block_unavailable(self):
        item = self.media["audio"]
        item["transcript"] = {"segments": [segment(0, 4), segment(5, 8)]}
        item["feedback"] = {"segments": [segment(4, 5, "exclude")]}
        for start, end in ((0, 4), (5, 8), (0, 8)):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                validate_storyboard(document([entry("audio", "audio", start, end)]), self.media)
        item["feedback"]["segments"].append(segment(1, 2))
        with self.assertRaises(ValueError):
            validate_storyboard(document([entry("audio", "audio", 0, 8, required=True)]), self.media)
        with self.assertRaises(ValueError):
            validate_storyboard(document(), self.media)

    def test_invalid_transcript_timing_is_never_silently_clipped(self):
        for segments in ([segment(-6, 1)], [segment(29, 31)], [segment(1, 1)],
                         [segment(True, 2)], [segment(0, float("nan"))], [None]):
            self.media["audio"]["transcript"] = {"segments": segments}
            with self.subTest(segments=segments), self.assertRaises(ValueError):
                validate_storyboard(document([entry("audio", "audio", 0, 2)]), self.media)

    def test_item_limit_is_an_error_not_silent_truncation(self):
        media = {str(index): {"id": str(index), "kind": "image"} for index in range(501)}
        invalid = document([entry(str(index), duration=0.1) for index in range(501)])
        with self.assertRaisesRegex(ValueError, "500"):
            validate_storyboard(invalid, media)
        self.assertEqual(len(invalid["items"]), 501)

    def test_constraint_helper_rejects_malformed_or_conflicting_ids(self):
        self.assertEqual(validate_constraints(["a"], ["b"]), ({"a"}, {"b"}))
        self.assertEqual(validate_constraints(), (set(), set()))
        for required, excluded in (("a", []), (["a", "a"], []), ([""], []),
                                   ([None], []), (["a"], ["a"]), ([], [" "])):
            with self.subTest(required=required, excluded=excluded), self.assertRaises(ValueError):
                validate_constraints(required, excluded)
