"""Isolated FilmCraft transport boundaries; no editor or media is launched."""

import asyncio
from contextlib import asynccontextmanager
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from storylens.filmcraft_transport import FilmCraft, _decode_result, _load_sdk


class Model(SimpleNamespace):
    """Small SDK-shaped model for tests that also run without the optional SDK."""

    def model_dump(self, **kwargs):
        aliases = {"structured_content": "structuredContent", "is_error": "isError",
                   "server_info": "serverInfo", "input_schema": "inputSchema",
                   "next_cursor": "nextCursor"}

        def value(item):
            if isinstance(item, Model):
                return item.model_dump(**kwargs)
            if isinstance(item, list):
                return [value(child) for child in item]
            return item

        return {aliases.get(key, key): value(item) for key, item in vars(self).items()}


def result(value=None, *, structured=None, content=None, error=False):
    if content is None:
        content = [] if value is None else [Model(type="text", text=json.dumps(value))]
    return Model(structured_content=structured, content=content, is_error=error)


class FilmCraftParserTests(unittest.TestCase):
    def test_text_objects_and_real_command_list_arrays_are_lossless(self):
        document = {"clips": [{"id": str(index), "name": "映像"} for index in range(140)]}
        self.assertEqual(_decode_result(result(document)), document)
        commands = [{"id": "sequence.inspect", "params": {"description": "Read only"}}]
        self.assertEqual(_decode_result(result(commands)), commands)

    def test_structured_result_and_matching_text_are_accepted(self):
        document = {"sequence": None, "extra": {"version": 9}}
        self.assertIs(_decode_result(result(structured=document)), document)
        self.assertIs(_decode_result(result(document, structured=document)), document)

    def test_ambiguous_and_noncontainer_results_are_rejected(self):
        cases = [result(42), result("plain text"), result([], structured={}), result(),
                 result(structured=123),
                 result({"value": True}, structured={"value": 1}),
                 result(content=[Model(type="text", text="{}"), Model(type="text", text="{}")]),
                 result(content=[Model(type="image", data="AA==")]),
                 result(content=[Model(type="text", text='{"x": 1, "x": 2}')]),
                 result(content=[Model(type="text", text='{"x": NaN}')]),
                 result(content=[Model(type="text", text="{incomplete")])]
        for response in cases:
            with self.subTest(response=response), self.assertRaises(RuntimeError):
                _decode_result(response)

    def test_oversized_text_and_structured_results_are_rejected_not_truncated(self):
        for response in (result({"large": "é" * (1024 * 1024)}),
                         result(structured={"large": "x" * (2 * 1024 * 1024)})):
            with self.subTest(kind=bool(response.content)):
                with self.assertRaisesRegex(RuntimeError, "2 MiB"):
                    _decode_result(response)

    def test_tool_errors_retain_partial_results(self):
        partial = {"completed": 1, "failed": 1,
                   "results": [{"id": "first", "ok": True}, {"error": "invalid clip"}]}
        with self.assertRaisesRegex(RuntimeError, "FilmCraft tool error") as raised:
            _decode_result(result(partial, error=True))
        self.assertIn("invalid clip", str(raised.exception))
        self.assertIn("completed", str(raised.exception))

    def test_sdk_import_is_lazy_and_missing_sdk_error_is_actionable(self):
        original_import = __import__

        def without_sdk(name, *args, **kwargs):
            if name == "mcp" or name.startswith("mcp."):
                raise ImportError("intentionally unavailable")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=without_sdk):
            spec = importlib.util.spec_from_file_location(
                "isolated_filmcraft_transport", Path(sys.modules[FilmCraft.__module__].__file__))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            with self.assertRaisesRegex(RuntimeError, r"install storylens\[mcp\]"):
                module._load_sdk()



class FilmCraftTransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        # The mocked transport never launches it, but this is a real executable
        # on every supported OS rather than a POSIX chmod-dependent fixture.
        self.binary = Path(sys.executable).resolve()
        self.events = []
        self.parameters = None
        self.initialize_error = None
        self.call_error = None
        self.block = None
        self.pages = [Model(tools=[Model(name="doc_inspect", input_schema={"type": "object"},
                                       description="actual definition", custom={"retained": True})],
                            next_cursor=None)]
        self.cursors = []
        self.calls = []
        self.response = result({"ok": True})
        owner = self

        class Session:
            def __init__(self, read, write):
                owner.assertEqual((read, write), ("read", "write"))

            async def __aenter__(self):
                owner.events.append("session enter")
                return self

            async def __aexit__(self, *args):
                owner.events.append("session exit")

            async def initialize(self):
                if owner.block == "initialize":
                    await asyncio.Event().wait()
                if owner.initialize_error:
                    raise owner.initialize_error
                return Model(server_info=Model(name="FilmCraft", version="0.9.0"),
                             capabilities={"tools": {}}, protocol_version="2025-11-25")

            async def list_tools(self, *, params=None):
                owner.cursors.append(params.cursor)
                if owner.block == "list":
                    await asyncio.Event().wait()
                return owner.pages.pop(0)

            async def call_tool(self, name, arguments):
                owner.calls.append((name, arguments))
                if owner.block == "call":
                    await asyncio.Event().wait()
                if owner.call_error:
                    raise owner.call_error
                if name == "command_list":
                    return result([{"id": "sequence.inspect", "params": []}])
                if name == "doc_inspect":
                    return result({"sequence": None, "bins": []})
                return owner.response

        @asynccontextmanager
        async def stdio(parameters):
            owner.parameters = parameters
            owner.events.append("stdio enter")
            try:
                if owner.block == "launch":
                    await asyncio.Event().wait()
                yield "read", "write"
            finally:
                owner.events.append("stdio exit")

        sdk = (Session, Model, stdio, Model)
        self.sdk_patch = patch("storylens.filmcraft_transport._load_sdk", return_value=sdk)
        self.sdk_patch.start()
        self.addCleanup(self.sdk_patch.stop)

    def assert_closed(self):
        self.assertEqual(self.events[-2:], ["session exit", "stdio exit"])

    async def test_discovery_drains_empty_cursor_and_preserves_actual_tool_schema(self):
        self.pages[0].next_cursor = ""
        self.pages.append(Model(tools=[Model(name="command_list", input_schema={"type": "object"})],
                                next_cursor="last"))
        self.pages.append(Model(tools=[], next_cursor=None))
        async with FilmCraft(self.binary) as client:
            discovery = await client.discover()
        self.assertEqual(self.cursors, [None, "", "last"])

    async def test_repeated_cursor_fails_and_closes_before_inspection_calls(self):
        self.pages[0].next_cursor = ""
        self.pages.append(Model(tools=[], next_cursor=""))
        async with FilmCraft(self.binary) as client:
            with self.assertRaisesRegex(RuntimeError, "repeated cursor"):
                await client.discover()
            self.assert_closed()
        self.assertEqual(self.calls, [])

    async def test_initialization_exception_closes_both_contexts(self):
        self.initialize_error = ValueError("unsupported protocol")
        with self.assertRaisesRegex(RuntimeError, "unsupported protocol"):
            async with FilmCraft(self.binary):
                self.fail("initialization should fail")
        self.assert_closed()

    async def test_launch_timeout_cleans_entering_transport(self):
        self.block = "launch"
        with self.assertRaisesRegex(RuntimeError, "initialization timed out"):
            async with FilmCraft(self.binary, timeout=0.01):
                self.fail("launch should time out")
        self.assertEqual(self.events, ["stdio enter", "stdio exit"])

    async def test_initialize_list_and_call_timeouts_close_session(self):
        for operation in ("initialize", "list", "call"):
            with self.subTest(operation=operation):
                self.events.clear()
                self.block = operation
                with self.assertRaisesRegex(RuntimeError, "timed out"):
                    async with FilmCraft(self.binary, timeout=0.01) as client:
                        if operation == "list":
                            await client.discover()
                        else:
                            await client.call("example", {})
                self.assert_closed()

    async def test_protocol_tool_and_parser_failures_close_session(self):
        for response, error in ((result({"error": "denied"}, error=True), None),
                                (result(123), None),
                                (result({}), ValueError("JSON-RPC invalid params"))):
            with self.subTest(response=response, error=error):
                self.events.clear()
                self.response, self.call_error = response, error
                async with FilmCraft(self.binary) as client:
                    with self.assertRaisesRegex(RuntimeError, "FilmCraft tool 'example' failed"):
                        await client.call("example", {})
                    self.assert_closed()

    async def test_cancelled_call_closes_session_and_preserves_cancellation(self):
        self.block = "call"
        with self.assertRaises(asyncio.CancelledError):
            async with FilmCraft(self.binary) as client:
                asyncio.get_running_loop().call_soon(asyncio.current_task().cancel)
                await client.call("example", {})
        self.assert_closed()

    async def test_context_body_exception_still_cleans_up(self):
        with self.assertRaisesRegex(ValueError, "caller failure"):
            async with FilmCraft(self.binary):
                raise ValueError("caller failure")
        self.assert_closed()

    def test_absent_relative_directory_and_nonexecutable_binary_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "absolute"):
            FilmCraft(Path("filmcraft-cli"))
        for binary in (self.root / "absent", self.root):
            with self.subTest(binary=binary), self.assertRaisesRegex(RuntimeError, "executable file"):
                FilmCraft(binary)
        # Windows chmod only changes the read-only flag, not execute access.
        # Exercise denied access without relying on POSIX permission bits.
        with patch("storylens.filmcraft_transport.os.access", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "executable file"):
                FilmCraft(self.binary)

    def test_invalid_timeout_is_rejected(self):
        for timeout in (0, -1, float("inf"), float("nan")):
            with self.subTest(timeout=timeout), self.assertRaisesRegex(ValueError, "positive finite"):
                FilmCraft(self.binary, timeout=timeout)
