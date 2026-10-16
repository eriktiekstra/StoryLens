"""Real official-SDK curation behavior and approved-root isolation over stdio."""

from contextlib import asynccontextmanager
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

import jsonschema
from PIL import Image

from storylens.config import Config
from storylens.service import Analyzer

HAS_MCP = importlib.util.find_spec("mcp") is not None
if HAS_MCP:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.shared.exceptions import MCPError


@unittest.skipUnless(HAS_MCP, "optional official mcp SDK required")
class CurationMCPTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.approved = self.root / "approved"
        self.approved.mkdir()
        self.private = self.root / "private"
        self.private.mkdir()
        self.config = Config(self.root / "catalog.sqlite3")
        self.paths = {}
        fixtures = (
            ("first", "2026:10:15 10:00:00", (10, 30, 50)),
            ("second", "2026:10:15 10:01:00", (60, 80, 100)),
            ("next_day", "2026:10:16 10:00:00", (110, 130, 150)),
            ("undated", None, (160, 180, 200)),
        )
        for name, capture, color in fixtures:
            self.paths[name] = self.approved / (name + ".jpg")
            self.image(self.paths[name], color, capture)
        with Analyzer(self.config) as app:
            app.scan(self.approved, analyze=True, stage="metadata")
            self.ids = {name: app.catalog.known_path(path)["media_id"]
                        for name, path in self.paths.items()}
        self.assertEqual(len(set(self.ids.values())), 4)

    @staticmethod
    def image(path, color, capture):
        exif = Image.Exif()
        if capture is not None:
            exif[36867] = capture  # DateTimeOriginal, not the filesystem timestamp.
        with Image.new("RGB", (32, 24), color) as image:
            image.save(path, exif=exif)

    def seed_private(self, *, manual=False, mixed=False):
        path = self.private / "secret.jpg"
        self.image(path, (210, 20, 70), "2026:10:15 10:00:30")
        with Analyzer(self.config) as app:
            app.scan(self.private, analyze=True, stage="metadata")
            private_id = app.catalog.known_path(path)["media_id"]
            event = None
            if manual:
                event = app.create_event("Confidential private gathering")
                app.set_feedback(private_id, {"event_id": event["id"]})
                if mixed:
                    app.set_feedback(self.ids["first"], {
                        "selection": "include", "event_id": event["id"]})
        return private_id, event

    @asynccontextmanager
    async def session(self):
        params = StdioServerParameters(command=sys.executable, args=[
            "-m", "storylens", "--database", str(self.config.database),
            "serve", "--allow-root", str(self.approved)])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                self.assertEqual(initialized.server_info.name, "StoryLens")
                resource = await session.read_resource("storylens://schemas/curation-v1")
                self.schema = json.loads(resource.contents[0].text)
                jsonschema.Draft202012Validator.check_schema(self.schema)
                yield session

    async def call(self, session, tool, arguments=None):
        response = await session.call_tool(tool, arguments or {})
        self.assertFalse(response.is_error, response.content)
        self.assertIsInstance(response.structured_content, dict)
        jsonschema.validate(response.structured_content, self.schema)
        return response.structured_content

    async def denied(self, session, tool, arguments, *, forbidden=()):
        # Authorization failures must be actual tool errors, not success envelopes.
        response = await session.call_tool(tool, arguments)
        self.assertTrue(response.is_error, response)
        self.assertTrue(response.content)
        serialized = response.model_dump_json()
        for value in forbidden:
            self.assertNotIn(value, serialized)
        return response

    async def invalid(self, session, tool, arguments):
        try:
            await self.denied(session, tool, arguments)
        except MCPError:
            pass  # SDK input validation can instead use JSON-RPC InvalidParams.

    async def members(self, session, event_id):
        result = await self.call(session, "show_event", {"event_id": event_id})
        self.assertIsNone(result["next_cursor"])
        return {row["media_id"]: row for row in result["items"]}

    async def test_event_and_member_pages_schema_and_cursor_scope(self):
        async with self.session() as session:
            complete = await self.call(session, "list_events")
            self.assertEqual(len(complete["items"]), 3)
            self.assertEqual(sorted(row["member_count"] for row in complete["items"]), [1, 1, 2])
            first = await self.call(session, "list_events", {"limit": 1})
            self.assertEqual(len(first["items"]), 1)
            self.assertIsNotNone(first["next_cursor"])
            second = await self.call(session, "list_events", {
                "limit": 1, "after": first["next_cursor"]})
            third = await self.call(session, "list_events", {
                "limit": 1, "after": second["next_cursor"]})
            self.assertEqual(first["items"] + second["items"] + third["items"], complete["items"])
            self.assertIsNone(third["next_cursor"])
            burst = next(row for row in complete["items"] if row["member_count"] == 2)
            self.assertEqual(burst["kind"], "automatic")
            self.assertEqual(burst["timezone_status"], "timezone_ambiguous")
            self.assertEqual(burst["title"], "2026-10-15")
            page = await self.call(session, "show_event", {"event_id": burst["id"], "limit": 1})
            tail = await self.call(session, "show_event", {
                "event_id": burst["id"], "limit": 1, "after": page["next_cursor"]})
            self.assertEqual({row["media_id"] for row in page["items"] + tail["items"]},
                             {self.ids["first"], self.ids["second"]})
            self.assertIsNone(tail["next_cursor"])
            for row in page["items"] + tail["items"]:
                self.assertEqual(row["evidence"]["capture_time"]["timezone_status"], "timezone_ambiguous")
                self.assertIsNotNone(row["evidence"]["capture_time"]["value"])
            other = next(row for row in complete["items"] if row["id"] != burst["id"])
            for tool, arguments in (
                ("list_events", {"after": page["next_cursor"]}),
                ("show_event", {"event_id": burst["id"], "after": first["next_cursor"]}),
                ("show_event", {"event_id": other["id"], "after": page["next_cursor"]}),
            ):
                with self.subTest(tool=tool, arguments=arguments):
                    await self.denied(session, tool, arguments)
            await self.call(session, "set_feedback", {
                "media_id": self.ids["first"], "patch": {"selection": "include"}})
            await self.denied(session, "list_events", {"after": first["next_cursor"]})
            await self.denied(session, "show_event", {
                "event_id": burst["id"], "after": page["next_cursor"]})

    async def test_manual_events_feedback_null_omission_and_reset(self):
        media_id = self.ids["first"]
        async with self.session() as session:
            original = await self.call(session, "get_feedback", {"media_id": media_id})
            self.assertEqual(original["values"], {})
            event = await self.call(session, "create_event", {"title": "User trip"})
            renamed = await self.call(session, "rename_event", {
                "event_id": event["id"], "title": "User weekend"})
            self.assertEqual(renamed["id"], event["id"])
            self.assertEqual(renamed["title"], "User weekend")
            patch = {"selection": "include", "event_id": event["id"],
                     "capture_time": None, "gps": None}
            changed = await self.call(session, "set_feedback", {"media_id": media_id, "patch": patch})
            self.assertEqual(changed["values"], patch)
            self.assertEqual(await self.call(session, "get_feedback", {"media_id": media_id}), changed)
            shown = await self.call(session, "show_event", {"event_id": event["id"]})
            self.assertEqual(shown["event"]["kind"], "manual")
            self.assertEqual(shown["event"]["title"], "User weekend")
            self.assertEqual(shown["items"][0]["evidence"]["capture_time"]["value"], None)
            self.assertEqual(shown["items"][0]["selection"], "include")
            changed = await self.call(session, "set_feedback", {
                "media_id": media_id, "patch": {"selection": "exclude"}})
            self.assertEqual(changed["values"], {**patch, "selection": "exclude"})
            reset = await self.call(session, "reset_feedback", {
                "media_id": media_id, "fields": ["capture_time"]})
            self.assertNotIn("capture_time", reset["values"])
            self.assertIn("gps", reset["values"])
            restored = await self.members(session, event["id"])
            self.assertEqual(restored[media_id]["evidence"]["capture_time"]["value"], "2026-10-15T10:00:00")
            reset = await self.call(session, "reset_feedback", {"media_id": media_id, "fields": None})
            self.assertEqual(reset["values"], {})
            events = await self.call(session, "list_events")
            self.assertNotIn(event["id"], {row["id"] for row in events["items"]})
            self.assertEqual(sorted(row["member_count"] for row in events["items"]), [1, 1, 2])
            await self.call(session, "set_feedback", {"media_id": media_id, "patch": {"gps": None}})
            self.assertEqual((await self.call(session, "reset_feedback", {"media_id": media_id}))["values"], {})

    async def test_integer_preferences_change_grouping_and_invalid_patches_are_atomic(self):
        media_id = self.ids["first"]
        async with self.session() as session:
            before = await self.call(session, "get_preferences")
            old_page = await self.call(session, "list_events", {"limit": 1})
            changed = await self.call(session, "set_preferences", {"patch": {"gap_seconds": 30, "distance_km": 25}})
            self.assertEqual(changed["values"], {**before["values"], "gap_seconds": 30, "distance_km": 25})
            self.assertEqual(await self.call(session, "get_preferences"), changed)
            events = await self.call(session, "list_events")
            self.assertEqual(len(events["items"]), 4)
            self.assertTrue(all(row["member_count"] == 1 for row in events["items"]))
            await self.denied(session, "list_events", {"after": old_page["next_cursor"]})
            feedback = await self.call(session, "set_feedback", {
                "media_id": media_id, "patch": {"selection": "include"}})
            for tool, arguments in (
                ("set_preferences", {"patch": {"gap_seconds": True}}),
                ("set_preferences", {"patch": {"unknown": 1}}),
                ("set_feedback", {"media_id": media_id, "patch": {"selection": "exclude", "unknown": 1}}),
                ("reset_feedback", {"media_id": media_id, "fields": "capture_time"}),
                ("reset_feedback", {"media_id": media_id, "fields": ["unknown"]}),
            ):
                with self.subTest(tool=tool, arguments=arguments):
                    await self.invalid(session, tool, arguments)
                    self.assertEqual(await self.call(session, "get_preferences"), changed)
                    self.assertEqual(await self.call(session, "get_feedback", {"media_id": media_id}), feedback)
            await self.call(session, "set_preferences", {"patch": {"gap_seconds": 120}})
            self.assertEqual(sorted(row["member_count"] for row in
                                    (await self.call(session, "list_events"))["items"]), [1, 1, 2])

    async def test_private_sources_and_private_manual_events_are_not_disclosed_or_mutated(self):
        private_id, _ = self.seed_private()
        async with self.session() as session:
            events = await self.call(session, "list_events")
            self.assertEqual(sorted(row["member_count"] for row in events["items"]), [1, 1, 2])
            visible = set()
            for event in events["items"]:
                members = await self.members(session, event["id"])
                visible.update(members)
                self.assertNotIn(private_id, json.dumps(members))
            self.assertEqual(visible, set(self.ids.values()))
            self.assertNotIn(private_id, json.dumps(events))
        # Core can curate private media; the narrower subprocess cannot use that relationship.
        with Analyzer(self.config) as app:
            event = app.create_event("Secret private-only title")
            app.set_feedback(private_id, {"event_id": event["id"]})
        forbidden = (private_id, event["id"], event["title"], str(self.private))
        async with self.session() as session:
            events = await self.call(session, "list_events")
            for value in forbidden:
                self.assertNotIn(value, json.dumps(events))
            own = await self.call(session, "set_feedback", {
                "media_id": self.ids["first"], "patch": {"selection": "include"}})
            for tool, arguments in (
                ("get_feedback", {"media_id": private_id}),
                ("show_event", {"event_id": event["id"]}),
                ("rename_event", {"event_id": event["id"], "title": "Unauthorized rename"}),
                ("set_feedback", {"media_id": self.ids["first"],
                                  "patch": {"event_id": event["id"], "selection": "exclude"}}),
            ):
                with self.subTest(tool=tool):
                    await self.denied(session, tool, arguments, forbidden=forbidden)
            self.assertEqual(await self.call(session, "get_feedback", {"media_id": self.ids["first"]}), own)
        with Analyzer(self.config) as app:
            self.assertEqual(app.feedback(private_id)["values"], {"event_id": event["id"]})
            self.assertEqual(app.event(event["id"])["event"]["title"], event["title"])

    async def test_mixed_root_manual_events_fail_closed_until_owned_assignment_cleared(self):
        private_id, event = self.seed_private(manual=True, mixed=True)
        forbidden = (private_id, event["title"], str(self.private))
        async with self.session() as session:
            for tool, arguments in (
                ("list_events", {}),
                ("show_event", {"event_id": event["id"]}),
                ("get_feedback", {"media_id": self.ids["first"]}),
                ("rename_event", {"event_id": event["id"], "title": "Unauthorized rename"}),
                ("set_feedback", {"media_id": self.ids["first"], "patch": {"selection": "exclude"}}),
            ):
                with self.subTest(tool=tool):
                    await self.denied(session, tool, arguments, forbidden=forbidden)
            cleared = await self.call(session, "set_feedback", {
                "media_id": self.ids["first"], "patch": {"event_id": None}})
            self.assertEqual(cleared["values"], {"selection": "include", "event_id": None})
            events = await self.call(session, "list_events")
            self.assertEqual(sorted(row["member_count"] for row in events["items"]), [1, 1, 2])
            for value in forbidden + (event["id"],):
                self.assertNotIn(value, json.dumps(events))
            burst = next(row for row in events["items"] if row["member_count"] == 2)
            self.assertEqual(set(await self.members(session, burst["id"])), {self.ids["first"], self.ids["second"]})
        with Analyzer(self.config) as app:
            self.assertEqual(app.feedback(private_id)["values"], {"event_id": event["id"]})
            app.set_feedback(self.ids["first"], {"event_id": event["id"]})
        async with self.session() as session:
            cleared = await self.call(session, "reset_feedback", {
                "media_id": self.ids["first"], "fields": ["event_id"]})
            self.assertEqual(cleared["values"], {"selection": "include"})
            self.assertEqual(await self.call(session, "get_feedback", {"media_id": self.ids["first"]}), cleared)
            self.assertEqual(len((await self.call(session, "list_events"))["items"]), 3)

    async def test_feedback_segments_round_trip_and_overlap_rejected(self):
        audio_path = self.approved / "clip.wav"
        import wave
        frames = b"\x00" * 2 * 16000 * 20
        with wave.open(str(audio_path), "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(16000)
            writer.writeframes(frames)
        with Analyzer(self.config) as app:
            app.scan(self.approved, analyze=True, stage="all")
            media_id = app.catalog.known_path(audio_path)["media_id"]
        async with self.session() as session:
            event = await self.call(session, "create_event", {"title": "Segmented event"})
            patch = {
                "media_id": media_id, "patch": {
                    "event_id": event["id"],
                    "segments": [
                        {"start_seconds": 0.0, "end_seconds": 5.0, "selection": "include", "label": "intro"},
                        {"start_seconds": 5.0, "end_seconds": 10.0, "selection": "exclude", "label": "cut"},
                    ],
                },
            }
            changed = await self.call(session, "set_feedback", patch)
            self.assertEqual(len(changed["values"]["segments"]), 2)
            shown = await self.call(session, "show_event", {"event_id": event["id"]})
            member = shown["items"][0]
            self.assertIn("segments", member["evidence"])
            self.assertEqual(member["evidence"]["segments"]["source"], "feedback.segments")
            await self.denied(session, "set_feedback", {
                "media_id": media_id,
                "patch": {
                    "segments": [
                        {"start_seconds": 0.0, "end_seconds": 5.0, "selection": "include"},
                        {"start_seconds": 2.0, "end_seconds": 3.0, "selection": "exclude"},
                    ],
                },
            })

    async def test_invalid_preferences_and_cursors_reject(self):
        async with self.session() as session:
            await self.invalid(session, "set_preferences", {"patch": {"semantic_similarity": 1.2}})
            await self.invalid(session, "set_preferences", {"patch": {"semantic_depth": "fastest"}})
            await self.denied(session, "list_events", {"after": "not-a-cursor"})
            page = await self.call(session, "list_events", {"limit": 1})
            await self.call(session, "set_preferences", {"patch": {"gap_seconds": 30}})
            await self.denied(session, "list_events", {"after": page["next_cursor"]})
