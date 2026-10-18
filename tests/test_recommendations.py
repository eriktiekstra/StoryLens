"""Consumer-visible recommendation constraints, scoring and portable output."""

from copy import deepcopy
import json
import unittest

from storylens.recommendations import recommend
from storylens.storyboard import validate_storyboard


def media(identifier, kind="image", *, start=0, end=30, event=None, technical=None,
          segments=None, selection=None, transcript=None):
    item = {"id": identifier, "kind": kind, "analysis": {}, "feedback": {}, "event_id": event,
            "paths": [{"path": "/private/source/" + identifier}], "created_at": "private timestamp"}
    if kind != "image" and start is not None:
        item["analysis"][kind + "_technical"] = {"result": {
            "source_timing": {"origin_seconds": start, "end_seconds": end}, "measurements": {}}}
    if technical is not None:
        if kind == "image":
            item["analysis"]["image_technical"] = {"result": {"metrics": {"sharpness": technical,
                "exposure": {"clipped_shadows_fraction": 1 - technical, "clipped_highlights_fraction": 0}}}}
        elif kind == "audio":
            item["analysis"]["audio_technical"]["result"]["measurements"] = {"clipping_sample_fraction": 1 - technical}
    if segments is not None:
        item["feedback"]["segments"] = segments
    if selection is not None:
        item["feedback"]["selection"] = selection
    if transcript is not None:
        item["transcript"] = {"segments": [{"start_seconds": left, "end_seconds": right,
                                             "text": "private transcript words"} for left, right in transcript]}
    return item


def segment(start, end, selection="include"):
    return {"start_seconds": start, "end_seconds": end, "selection": selection}


def intervals(document, identifier=None):
    return [(entry["start_seconds"], entry["end_seconds"]) for entry in document["items"]
            if identifier is None or entry["media_id"] == identifier]


class RecommendationTests(unittest.TestCase):
    def validated(self, items, **kwargs):
        document = recommend(items, **kwargs)
        validate_storyboard(document, {item["id"]: item for item in items})
        return document

    def test_empty_catalog_is_a_portable_empty_storyboard(self):
        document = self.validated([])
        self.assertEqual(document["items"], [])
        self.assertEqual(document["total_seconds"], 0)
        self.assertEqual(document["provenance"]["algorithm"], "recommendations-1")
        self.assertTrue(document["provenance"]["caveats"])

    def test_repeated_calls_and_input_permutations_are_identical(self):
        items = [media("b", technical=0.8), media("a", technical=0.8), media("c", technical=0.8)]
        first = self.validated(items, duration_seconds=8)
        self.assertEqual(first, recommend(list(reversed(items)), duration_seconds=8))
        self.assertEqual(first, recommend(items, duration_seconds=8))
        self.assertEqual([part["media_id"] for part in first["items"]], ["a", "b"])

    def test_output_does_not_export_paths_timestamps_or_words_or_mutate_input(self):
        items = [media("audio", "audio", end=5, transcript=[(0, 5)])]
        original = deepcopy(items)
        document = self.validated(items)
        serialized = json.dumps(document, allow_nan=False)
        for private in ("/private/source/", "private timestamp", "private transcript words"):
            self.assertNotIn(private, serialized)
        self.assertEqual(items, original)

    def test_missing_scores_are_nullable_not_invented(self):
        document = self.validated([media("a")])
        scores = document["items"][0]["scores"]
        self.assertIsNone(scores["technical"])
        self.assertIsNone(scores["relevance"])
        self.assertEqual(scores["story"], 0)
        self.assertEqual(scores["total"], 0)

    def test_strategy_changes_selection_without_conflating_scores(self):
        items = [media("quality", event="common", technical=1),
                 media("other", event="common", technical=0),
                 media("rare", event="rare", technical=0.4)]
        quality = self.validated(items, strategy="technical", duration_seconds=4)["items"][0]
        story = self.validated(items, strategy="story", duration_seconds=4)["items"][0]
        self.assertEqual(quality["media_id"], "quality")
        self.assertEqual(story["media_id"], "rare")
        self.assertEqual(quality["scores"]["technical"], 1)
        self.assertEqual(story["scores"]["story"], 0.5)
        self.assertIsNone(story["scores"]["relevance"])

    def test_cosine_mapping_and_custom_weights(self):
        items = [media("a", technical=1), media("b", technical=0)]
        document = self.validated(items, duration_seconds=4, relevance={"a": -1, "b": 1},
                                  weights={"technical": 0, "relevance": 7, "story": 0})
        self.assertEqual(document["weights"], {"technical": 0, "relevance": 1, "story": 0})
        self.assertEqual(document["items"][0]["media_id"], "b")
        self.assertEqual(document["items"][0]["scores"]["relevance"], 1)
        self.assertEqual(document["items"][0]["scores"]["total"], 1)
        neutral = recommend([media("a")], relevance={"a": 0})
        self.assertEqual(neutral["items"][0]["scores"]["relevance"], 0.5)

    def test_missing_components_renormalize_only_available_weights(self):
        document = self.validated([media("a", technical=0.8)],
                                  weights={"technical": 1, "relevance": 8, "story": 1})
        self.assertAlmostEqual(document["items"][0]["scores"]["total"], 0.4)
        no_evidence = self.validated([media("a")], weights={"technical": 1, "relevance": 0, "story": 0})
        self.assertEqual(no_evidence["items"][0]["scores"]["total"], 0)

    def test_large_finite_weights_normalize_without_overflow(self):
        document = self.validated([media("a")], weights={"technical": 1e308, "relevance": 1e308, "story": 1e308})
        self.assertAlmostEqual(sum(document["weights"].values()), 1)

    def test_audio_clipping_and_video_interval_sample_scores(self):
        audio = self.validated([media("a", "audio", end=1, technical=0.75)])
        self.assertEqual(audio["items"][0]["scores"]["technical"], 0.75)
        video = media("v", "video", end=20)
        video["analysis"]["video_technical"]["result"]["measurements"] = {"frames": {"samples": [
            {"source_timestamp_seconds": 1, "laplacian_variance_blur_proxy": 0,
             "dark_pixel_fraction": 1, "bright_pixel_fraction": 0},
            {"source_timestamp_seconds": 11, "laplacian_variance_blur_proxy": 100,
             "dark_pixel_fraction": 0, "bright_pixel_fraction": 0}]}}
        document = self.validated([video], duration_seconds=10, strategy="technical")
        self.assertEqual(intervals(document), [(10, 20)])
        self.assertEqual(document["items"][0]["scores"]["technical"], 0.75)

    def test_source_scenes_are_candidates_beyond_first_clip(self):
        video = media("v", "video", start=5, end=25)
        video["analysis"]["video_technical"]["result"]["scenes"] = [
            {"start_seconds": 5, "end_seconds": 8}, {"start_seconds": 8, "end_seconds": 25}]
        document = self.validated([video], duration_seconds=20)
        self.assertEqual(intervals(document), [(5, 8), (8, 18), (18, 25)])

    def test_required_images_allocate_before_optional_higher_scores(self):
        items = [media("a", technical=1), media("z", technical=0)]
        document = self.validated(items, required=["z"], duration_seconds=4)
        self.assertEqual([part["media_id"] for part in document["items"]], ["z"])
        self.assertTrue(document["items"][0]["required"])
        self.assertEqual(document["required"], ["z"])

    def test_feedback_and_request_constraints_merge(self):
        items = [media("a", selection="include"), media("b", selection="exclude"), media("c")]
        document = self.validated(items, required=["c"], duration_seconds=8)
        self.assertEqual(document["required"], ["a", "c"])
        self.assertEqual(document["excluded"], ["b"])
        self.assertEqual([part["media_id"] for part in document["items"]], ["a", "c"])

    def test_conflicting_unknown_and_duplicate_requested_ids_fail(self):
        items = [media("a", selection="exclude")]
        for kwargs in ({"required": ["missing"]}, {"excluded": ["missing"]}, {"required": ["a"]},
                       {"required": ["a"], "excluded": ["a"]}, {"excluded": ["a", "a"]}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                recommend(items, **kwargs)
        with self.assertRaises(ValueError):
            recommend([media("a", selection="include")], excluded=["a"])

    def test_mandatory_segments_are_the_union_not_a_single_clip(self):
        item = media("a", "audio", segments=[segment(1, 4), segment(3, 7), segment(12, 14)])
        document = self.validated([item], duration_seconds=8)
        self.assertEqual(intervals(document), [(1, 7), (12, 14)])
        self.assertEqual(document["required"], [])
        self.assertTrue(all(part["required"] for part in document["items"]))
        with self.assertRaises(ValueError):
            recommend([item], duration_seconds=7)

    def test_segment_exclusions_are_half_open_and_partition_candidates(self):
        item = media("a", "audio", start=-5, end=15, segments=[segment(-2, 2, "exclude")])
        document = self.validated([item], duration_seconds=20)
        self.assertEqual(intervals(document), [(-5, -2), (2, 12), (12, 15)])
        self.assertEqual(document["total_seconds"], 16)
        touching = media("a", "audio", end=10, segments=[segment(0, 5), segment(5, 10, "exclude")])
        self.assertEqual(intervals(self.validated([touching], duration_seconds=5)), [(0, 5)])

    def test_whole_exclusion_and_mandatory_segment_conflict(self):
        item = media("a", "audio", segments=[segment(1, 2)])
        with self.assertRaises(ValueError):
            recommend([item], excluded=["a"])

    def test_nonzero_and_negative_origins_preserve_absolute_source_time(self):
        for start in (-12.5, 7.5):
            with self.subTest(start=start):
                item = media("a", "audio", start=start, end=start + 12)
                document = self.validated([item], duration_seconds=12)
                self.assertEqual(intervals(document), [(start, start + 10), (start + 10, start + 12)])

    def test_metadata_timing_is_used_without_technical_analysis(self):
        item = media("a", "audio", start=None)
        item["analysis"]["metadata"] = {"result": {"streams": [
            {"codec_type": "audio", "start_time": "-3", "duration": "5"}]}}
        self.assertEqual(intervals(self.validated([item], duration_seconds=5)), [(-3, 2)])

    def test_unknown_timing_skips_optional_but_errors_required(self):
        item = media("a", "audio", start=None)
        document = self.validated([item])
        self.assertEqual(document["items"], [])
        self.assertTrue(any("timing" in warning for warning in document["warnings"]))
        with self.assertRaises(ValueError):
            recommend([item], required=["a"])

    def test_conversation_union_merges_overlap_and_two_second_gaps(self):
        item = media("a", "audio", start=5, end=30, transcript=[(6, 10), (9, 14), (16, 19)])
        document = self.validated([item], duration_seconds=13, clip_seconds=3)
        self.assertEqual(intervals(document), [(6, 19)])
        self.assertEqual(document["items"][0]["scores"]["story"], 0.5)

    def test_required_segment_expands_to_atomic_conversation(self):
        item = media("a", "video", start=5, end=25, transcript=[(6, 10), (12, 20)], segments=[segment(8, 9)])
        document = self.validated([item], duration_seconds=14)
        self.assertEqual(intervals(document), [(6, 20)])
        self.assertTrue(document["items"][0]["required"])
        with self.assertRaises(ValueError):
            recommend([item], duration_seconds=13)

    def test_optional_oversized_conversation_is_skipped_not_trimmed(self):
        item = media("a", "audio", end=20, transcript=[(0, 20)])
        document = self.validated([item], duration_seconds=19)
        self.assertEqual(document["items"], [])
        self.assertTrue(any("atomic conversation" in warning for warning in document["warnings"]))
        with self.assertRaises(ValueError):
            recommend([item], duration_seconds=19, required=["a"])

    def test_exclusion_interrupting_conversation_removes_entire_block(self):
        item = media("a", "audio", end=20, transcript=[(2, 12)], segments=[segment(8, 9, "exclude")])
        document = self.validated([item], duration_seconds=20)
        self.assertEqual(intervals(document), [(0, 2), (12, 20)])
        self.assertTrue(document["warnings"])
        item["feedback"]["segments"].append(segment(3, 4))
        with self.assertRaises(ValueError):
            recommend([item])

    def test_imported_transcript_takes_precedence_over_model_output(self):
        item = media("a", "audio", end=20, transcript=[(1, 5)])
        item["analysis"]["audio_transcription"] = {"result": {"segments": [
            {"start_seconds": 0, "end_seconds": 20}]}}
        document = self.validated([item], duration_seconds=4)
        self.assertEqual(intervals(document), [(1, 5)])
        item["transcript"] = {"segments": []}
        no_speech = self.validated([item], duration_seconds=10)
        self.assertEqual(intervals(no_speech), [(0, 10)])

    def test_catalog_imported_stage_and_model_fallback_both_preserve_blocks(self):
        for stage in ("transcript", "audio_transcription"):
            item = media("a", "audio", end=20)
            item["analysis"][stage] = {"result": {"segments": [{"start_seconds": 0, "end_seconds": 20}]}}
            with self.subTest(stage=stage):
                self.assertEqual(self.validated([item], duration_seconds=19)["items"], [])

    def test_conversation_must_not_expand_outside_source(self):
        item = media("a", "audio", start=5, end=10, transcript=[(4, 8)])
        self.assertEqual(self.validated([item])["items"], [])
        with self.assertRaises(ValueError):
            recommend([item], required=["a"])

    def test_whole_media_required_need_not_consume_entire_source(self):
        item = media("a", "audio", end=100)
        document = self.validated([item], required=["a"], duration_seconds=10)
        self.assertEqual(intervals(document), [(0, 10)])

    def test_required_selection_chooses_feasible_short_candidate_and_reserves_others(self):
        items = [media("a", "audio", end=12, transcript=[(0, 10)]), media("b")]
        document = self.validated(items, required=["a", "b"], duration_seconds=6)
        self.assertEqual(intervals(document, "a"), [(10, 12)])
        self.assertEqual(document["total_seconds"], 6)

    def test_playback_order_is_identity_time_not_score_or_requirement_order(self):
        items = [media("z", technical=1), media("a", technical=0)]
        document = self.validated(items, required=["z"], duration_seconds=8)
        self.assertEqual([part["media_id"] for part in document["items"]], ["a", "z"])

    def test_images_once_and_source_intervals_never_repeat_or_overlap(self):
        items = [media("a", "audio", end=20, segments=[segment(4, 7)]), media("b")]
        document = self.validated(items)
        self.assertEqual(sum(part["media_id"] == "b" for part in document["items"]), 1)
        spans = intervals(document, "a")
        self.assertTrue(all(first[1] <= second[0] for first, second in zip(spans, spans[1:])))
        self.assertEqual(sum(right - left for left, right in spans), 20)

    def test_more_than_500_selected_entries_errors_instead_of_truncating(self):
        items = [media(f"{index:03}") for index in range(501)]
        self.assertEqual(len(self.validated(items[:500], duration_seconds=2000)["items"]), 500)
        with self.assertRaises(ValueError):
            recommend(items, duration_seconds=2004)

    def test_tiny_clip_duration_fails_without_unbounded_candidate_generation(self):
        item = media("a", "video", end=4)
        for clip_seconds in (1e-100, 0.000001):
            with self.subTest(clip_seconds=clip_seconds), self.assertRaises(ValueError):
                recommend([item], clip_seconds=clip_seconds)

    def test_invalid_settings_fail_before_selection(self):
        for name in ("duration_seconds", "image_seconds", "clip_seconds"):
            for invalid in (0, -1, True, "4", float("nan"), float("inf"), 10 ** 1000):
                with self.subTest(name=name, value=invalid), self.assertRaises(ValueError):
                    recommend([], **{name: invalid})
        for invalid in ("unknown", None, []):
            with self.subTest(strategy=invalid), self.assertRaises(ValueError):
                recommend([], strategy=invalid)
        for weights in ({}, {"technical": 1}, {"technical": -1, "relevance": 1, "story": 1},
                        {"technical": 0, "relevance": 0, "story": 0},
                        {"technical": True, "relevance": 1, "story": 1},
                        {"technical": float("nan"), "relevance": 1, "story": 1}):
            with self.subTest(weights=weights), self.assertRaises(ValueError):
                recommend([], weights=weights)
        for relevance in ({"a": True}, {"a": -1.1}, {"a": 1.1}, {"a": float("inf")}, []):
            with self.subTest(relevance=relevance), self.assertRaises(ValueError):
                recommend([], relevance=relevance)

    def test_duplicate_media_identity_is_an_error_not_repeated_playback(self):
        with self.assertRaises(ValueError):
            recommend([media("a"), media("a")])

