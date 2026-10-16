"""Real subprocess MCP interoperability, authorization and shared CLI behavior."""

import asyncio
from contextlib import asynccontextmanager
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from PIL import Image

from storylens.config import Config
from storylens.service import Analyzer
from storylens.transcripts import serialize

HAS_MCP = importlib.util.find_spec("mcp") is not None
if HAS_MCP:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.shared.exceptions import MCPError


@unittest.skipUnless(HAS_MCP, "optional official mcp SDK required")
class MCPTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.media = self.root / "approved"
        self.media.mkdir()
        self.outside = self.root / "private"
        self.outside.mkdir()
        self.config = Config(self.root / "catalog.sqlite3")
        self.image = self.media / "photo.png"
        Image.new("RGB", (32, 24), (12, 34, 56)).save(self.image)

    @asynccontextmanager
    async def session(self):
        params = StdioServerParameters(command=sys.executable, args=["-m", "storylens", "--database",
            str(self.config.database), "serve", "--allow-root", str(self.media)])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                self.assertEqual(initialized.server_info.name, "StoryLens")
                yield session

    async def call(self, session, tool, arguments=None):
        response = await session.call_tool(tool, arguments or {})
        self.assertFalse(response.is_error, response.content)
        self.assertIsInstance(response.structured_content, dict)
        return response.structured_content

    async def failed(self, session, tool, arguments):
        try:
            response = await session.call_tool(tool, arguments)
        except MCPError:
            return  # SDK malformed arguments can be JSON-RPC InvalidParams.
        self.assertTrue(response.is_error, response)
        self.assertTrue(response.content)

    async def finish(self, session, job_id):
        async with asyncio.timeout(30):
            while True:
                state = await self.call(session, "job_status", {"job_id": job_id})
                if state["status"] not in {"queued", "running", "cancel_requested"}:
                    return state
                await asyncio.sleep(0.05)

    async def test_stdio_handshake_scan_analysis_and_cli_parity(self):
        original = hashlib.sha256(self.image.read_bytes()).hexdigest()
        async with self.session() as session:
            scan = await self.call(session, "start_job", {"path": str(self.media), "operation": "scan"})
            done = await self.finish(session, scan["id"])
            self.assertEqual(done["status"], "succeeded", done)
            self.assertEqual(done["summary"]["indexed"], 1)
            analysis = await self.call(session, "start_job", {"path": str(self.media), "operation": "analyze"})
            done = await self.finish(session, analysis["id"])
            self.assertEqual(done["status"], "succeeded", done)
            self.assertEqual(done["summary"]["stages"]["image_technical"]["analyzed"], 1)
            result = await self.call(session, "job_result", {"job_id": analysis["id"]})
            self.assertEqual(result["summary"], done["summary"])
            listed = await self.call(session, "list_media", {"limit": 1})
            media_id = listed["items"][0]["id"]
            shown = await self.call(session, "show_media", {"media_id": media_id})
            exported = await self.call(session, "export_media", {"limit": 1})
            self.assertEqual(exported["items"][0], shown["media"])
            jobs = await self.call(session, "list_jobs", {"limit": 1})
            self.assertEqual(len(jobs["items"]), 1)
            self.assertIsNotNone(jobs["next_cursor"])
            second = await self.call(session, "list_jobs", {"limit": 1, "after": jobs["next_cursor"]})
            self.assertNotEqual(jobs["items"][0]["id"], second["items"][0]["id"])
            resources = {str(resource.uri) for resource in (await session.list_resources()).resources}
            self.assertIn("storylens://schemas/results-v1", resources)
            self.assertIn("storylens://schemas/jobs-v1", resources)
            import jsonschema
            core_schema = json.loads((await session.read_resource("storylens://schemas/results-v1")).contents[0].text)
            job_schema = json.loads((await session.read_resource("storylens://schemas/jobs-v1")).contents[0].text)
            jsonschema.Draft202012Validator.check_schema(core_schema)
            jsonschema.Draft202012Validator.check_schema(job_schema)
            jsonschema.validate(listed, core_schema)
            jsonschema.validate(exported, core_schema)
            jsonschema.validate(done, job_schema)
            bounded_schema = json.loads((await session.read_resource("storylens://schemas/mcp-v1")).contents[0].text)
            jsonschema.Draft202012Validator.check_schema(bounded_schema)
            jsonschema.validate(shown, bounded_schema)
            jsonschema.validate(exported, bounded_schema)
        cli = await asyncio.to_thread(subprocess.run, [sys.executable, "-m", "storylens", "--database",
            str(self.config.database), "--json", "show", media_id], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(cli.stdout), shown)
        self.assertEqual(hashlib.sha256(self.image.read_bytes()).hexdigest(), original)

    async def test_scope_filters_identities_paths_and_duplicate_groups(self):
        private = self.outside / "hidden.png"
        Image.new("RGB", (16, 16), (200, 20, 10)).save(private)
        shared = self.outside / "same.png"
        shared.write_bytes(self.image.read_bytes())
        second = self.media / "copy.png"
        second.write_bytes(self.image.read_bytes())
        with Analyzer(self.config) as app:
            app.scan(self.media)
            app.scan(self.outside)
            private_id = app.catalog.known_path(private)["media_id"]
            allowed_id = app.catalog.known_path(self.image)["media_id"]
        async with self.session() as session:
            page = await self.call(session, "list_media", {"limit": 1})
            self.assertEqual([row["id"] for row in page["items"]], [allowed_id])
            self.assertIsNone(page["next_cursor"])
            await self.failed(session, "show_media", {"media_id": private_id})
            await self.failed(session, "scenes", {"media_id": private_id})
            await self.failed(session, "export_transcript", {"media_id": private_id})
            shown = await self.call(session, "show_media", {"media_id": allowed_id})
            self.assertEqual({row["path"] for row in shown["media"]["paths"]}, {str(self.image), str(second)})
            duplicate = await self.call(session, "duplicates")
            self.assertEqual(set(duplicate["items"][0]["paths"]), {str(self.image), str(second)})
            second.unlink()
            self.assertEqual((await self.call(session, "duplicates"))["items"], [])
            self.assertNotIn(str(self.outside), json.dumps(shown))

    async def test_transcript_import_lossless_character_pages_and_summary(self):
        source = self.media / "captions.json"
        source.write_text(json.dumps({"segments": [{"start": i, "end": i + 1,
            "text": "/private/" + "é" * 20000 if i == 0 else "Evidence " + str(i)}
            for i in range(150)]}), encoding="utf-8")
        with Analyzer(self.config) as app:
            app.scan(self.media)
            media_id = app.catalog.known_path(self.image)["media_id"]
        async with self.session() as session:
            imported = await self.call(session, "import_transcript", {"media_id": media_id, "path": str(source)})
            self.assertEqual(imported["segments"], 150)
            after, chunks = "", []
            while True:
                page = await self.call(session, "export_transcript", {"media_id": media_id, "after": after, "limit": 400})
                self.assertLessEqual(len(page["text"]), 400)
                chunks.append(page["text"])
                after = page["next_cursor"]
                if after is None:
                    break
            self.assertEqual(len(json.loads("".join(chunks))["segments"]), 150)
            with Analyzer(self.config) as app:
                self.assertEqual("".join(chunks), serialize(app.transcript(media_id), "json"))
            serialized = "".join(chunks)
            offset = serialized.index("/private/")
            maximum = await self.call(session, "export_transcript", {
                "media_id": media_id, "after": str(offset), "limit": 16384})
            self.assertEqual(maximum["text"], serialized[offset:offset + 16384])
            self.assertEqual(maximum["next_cursor"], str(offset + 16384))
            shown = await self.call(session, "show_media", {"media_id": media_id})
            transcript = shown["media"]["analysis"]["transcript"]["result"]
            self.assertNotIn("segments", transcript)
            self.assertEqual(transcript["segments_count"], 150)
            self.assertTrue(transcript["segments_omitted"])
            await self.failed(session, "import_transcript", {"media_id": media_id, "path": str(self.outside / "no.json")})

    async def test_unapproved_paths_symlinks_and_malformed_arguments(self):
        private = self.outside / "private.png"
        private.write_bytes(self.image.read_bytes())
        paths = [str(private), str(self.media / ".." / "private")]
        link = self.media / "escape.png"
        try:
            link.symlink_to(private)
        except OSError:
            pass  # Windows may withhold symlink privileges; other denials still run.
        else:
            paths.append(str(link))
        async with self.session() as session:
            for path in paths:
                await self.failed(session, "start_job", {"path": path, "operation": "scan"})
                await self.failed(session, "search", {"image": path})
            for args in ({"limit": 0}, {"limit": 101}, {"limit": "2"}, {"limit": True},
                         {"roots": [str(self.outside)]}, {"database": str(self.root / "other.sqlite3")}):
                await self.failed(session, "list_media", args)
            await self.failed(session, "show_media", {})
            await self.failed(session, "job_status", {"job_id": "missing"})
            await self.failed(session, "search", {"text": "a", "image": str(self.image)})
            # Invalid calls do not terminate the transport or escalate permissions.
            self.assertEqual((await self.call(session, "list_media"))["items"], [])

    async def test_durable_job_survives_transport_disconnect(self):
        async with self.session() as session:
            started = await self.call(session, "start_job", {"path": str(self.media), "operation": "scan"})
        async with self.session() as session:
            done = await self.finish(session, started["id"])
            self.assertEqual(done["status"], "succeeded", done)
            self.assertEqual(done["summary"]["indexed"], 1)

    async def test_scene_pages_and_large_metadata_are_bounded(self):
        with Analyzer(self.config) as app:
            app.scan(self.media)
            media_id = app.catalog.known_path(self.image)["media_id"]
            app.catalog.save(media_id, "video_technical", "fixture", {"provenance": {}, "scenes": [
                {"index": i, "representative_frame": {"path": str(self.outside / "frame.jpg")}}
                for i in range(250)]})
            app.catalog.save(media_id, "metadata", "fixture", {
                "filename": r"C:\private\frame.jpg", "unc_path": r"\\private-server\share\frame.jpg",
                "description": "x" * 2000000})
        async with self.session() as session:
            first = await self.call(session, "scenes", {"media_id": media_id, "limit": 100})
            self.assertEqual(len(first["items"]), 100)
            self.assertEqual(first["next_cursor"], "100")
            second = await self.call(session, "scenes", {"media_id": media_id, "limit": 100, "after": "100"})
            self.assertEqual(second["items"][0]["index"], 100)
            self.assertNotIn(str(self.outside), json.dumps(first))
            shown = await self.call(session, "show_media", {"media_id": media_id})
            self.assertTrue(shown["truncated"])
            self.assertLess(len(json.dumps(shown)), 140000)
            self.assertNotIn("private-server", json.dumps(shown))
            self.assertNotIn("private\\\\frame", json.dumps(shown))
            self.assertNotIn("scenes", shown["media"]["analysis"]["video_technical"]["result"])
            exported = await self.call(session, "export_media", {"limit": 1})
            self.assertIn("next_cursor", exported)
            import jsonschema
            schema = json.loads((await session.read_resource("storylens://schemas/mcp-v1")).contents[0].text)
            jsonschema.validate(shown, schema)
            jsonschema.validate(exported, schema)

    async def test_scoping_precedes_semantic_ranking_and_near_duplicate_candidates(self):
        from storylens.access import PathPolicy
        from storylens.mcp_server import _ScopedCatalog
        from storylens.semantics import search_results
        private = self.outside / "hidden.png"
        Image.new("RGB", (16, 16), (200, 20, 10)).save(private)
        with Analyzer(self.config) as app:
            app.scan(self.media, analyze=True)
            app.scan(self.outside, analyze=True)
            allowed_id = app.catalog.known_path(self.image)["media_id"]
            private_id = app.catalog.known_path(private)["media_id"]
            for media_id, vector in ((allowed_id, [0, 1]), (private_id, [1, 0])):
                app.catalog.save(media_id, "semantic_embeddings", "fixture", {
                    "provenance": {"space": "test", "depth": "fast", "algorithm": "semantics-v1"},
                    "items": [{"vector": vector, "evidence": {"kind": "image"}}]})
            app.catalog = _ScopedCatalog(app.catalog, PathPolicy([self.media]))
            ranked = search_results(app.catalog.semantic_results(), [1, 0], space="test", depth="fast", limit=1)
            self.assertEqual([row["media_id"] for row in ranked], [allowed_id])
            _, extractor = app._technical("image")
            self.assertEqual([row["id"] for row in app.catalog.image_results(extractor.cache_key())], [allowed_id])
