"""Portable storyboards remain complete and root-authorized over real SDK stdio."""

from contextlib import asynccontextmanager
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image

from storylens.config import Config
from storylens.service import Analyzer

HAS_MCP = importlib.util.find_spec("mcp") is not None
if HAS_MCP:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client


@unittest.skipUnless(HAS_MCP, "optional official mcp SDK required")
class StoryboardMCPTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.approved = self.root / "approved"
        self.private = self.root / "private"
        self.approved.mkdir()
        self.private.mkdir()
        self.config = Config(self.root / "catalog.sqlite3")
        Image.new("RGB", (8, 8), (1, 2, 3)).save(self.approved / "visible.png")
        Image.new("RGB", (8, 8), (4, 5, 6)).save(self.private / "secret.png")
        with Analyzer(self.config) as app:
            app.scan(self.approved)
            app.scan(self.private)
            self.visible_id = app.catalog.known_path(self.approved / "visible.png")["media_id"]
            self.private_id = app.catalog.known_path(self.private / "secret.png")["media_id"]
            app.set_feedback(self.private_id, {"selection": "include"})

    @asynccontextmanager
    async def session(self):
        params = StdioServerParameters(command=sys.executable, args=[
            "-m", "storylens", "--database", str(self.config.database),
            "serve", "--allow-root", str(self.approved)])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session

    async def test_private_required_feedback_is_filtered_before_recommendation(self):
        async with self.session() as session:
            response = await session.call_tool("recommend_storyboard", {"duration_seconds": 8})
            self.assertFalse(response.is_error, response.content)
            document = response.structured_content
            self.assertEqual({item["media_id"] for item in document["items"]}, {self.visible_id})
            self.assertNotIn(self.private_id, json.dumps(document))
            denied = await session.call_tool("recommend_storyboard", {"required": [self.private_id]})
            self.assertTrue(denied.is_error)
            document["items"][0]["media_id"] = self.private_id
            denied = await session.call_tool("validate_storyboard", {"document": document})
            self.assertTrue(denied.is_error)

    async def test_current_feedback_cannot_be_erased_from_edited_document(self):
        with Analyzer(self.config) as app:
            app.set_feedback(self.visible_id, {"selection": "include"})
        async with self.session() as session:
            response = await session.call_tool("recommend_storyboard", {"duration_seconds": 4})
            self.assertFalse(response.is_error, response.content)
            document = response.structured_content
            document.update(required=[], items=[], total_seconds=0)
            denied = await session.call_tool("validate_storyboard", {"document": document})
            self.assertTrue(denied.is_error)

    async def test_more_than_one_hundred_entries_are_not_transport_truncated(self):
        for index in range(110):
            Image.new("RGB", (8, 8), (index + 10, 20, 30)).save(self.approved / f"photo-{index}.png")
        with Analyzer(self.config) as app:
            app.scan(self.approved)
            expected = {row["id"] for row in app.catalog.page(500)["items"]} - {self.private_id}
        async with self.session() as session:
            response = await session.call_tool("recommend_storyboard", {"duration_seconds": 444})
            self.assertFalse(response.is_error, response.content)
            document = response.structured_content
            self.assertEqual({item["media_id"] for item in document["items"]}, expected)
            self.assertEqual(document["total_seconds"], 444)
            self.assertNotIn("truncated", document)
            checked = await session.call_tool("validate_storyboard", {"document": document})
            self.assertFalse(checked.is_error, checked.content)
            self.assertEqual(checked.structured_content, document)
            if importlib.util.find_spec("jsonschema"):
                import jsonschema
                resource = await session.read_resource("storylens://schemas/storyboard-v1")
                schema = json.loads(resource.contents[0].text)
                jsonschema.validate(document, schema)
