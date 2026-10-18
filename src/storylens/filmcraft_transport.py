"""Optional, explicit headless FilmCraft MCP transport.

Importing this module needs only the standard library. The executable is trusted
by the caller; discovery reads the running editor's surface, never edits it.
"""

import asyncio
from contextlib import AsyncExitStack
import json
import math
import os
from pathlib import Path
import sys


_MAX_RESULT_BYTES = 2 * 1024 * 1024


def _load_sdk():
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from mcp.types import PaginatedRequestParams
    except ImportError as exc:
        raise RuntimeError("FilmCraft requires the optional MCP SDK; install storylens[mcp]") from exc
    return ClientSession, StdioServerParameters, stdio_client, PaginatedRequestParams


def _environment():
    # The SDK itself also inherits its small platform allowlist. No full copy of
    # os.environ is passed: API credentials, OTEL exporters, and loader hooks stay
    # out of the child. HOME/application directories support native editor paths.
    names = ("PATH", "HOME", "TMPDIR") if sys.platform != "win32" else (
        "PATH", "SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "TEMP", "TMP",
        "APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
    )
    return {name: value for name in names
            if (value := os.environ.get(name)) is not None and not value.startswith("()")}


def _check_size(value):
    size = 0
    try:
        for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False).iterencode(value):
            size += len(chunk.encode("utf-8"))
            if size > _MAX_RESULT_BYTES:
                raise RuntimeError("FilmCraft result exceeds the 2 MiB limit")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise RuntimeError("FilmCraft returned invalid JSON data") from exc


def _object_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError(f"non-JSON constant: {value}")


def _parse_text(text):
    if not isinstance(text, str):
        raise RuntimeError("FilmCraft returned a malformed text block")
    if len(text.encode("utf-8")) > _MAX_RESULT_BYTES:
        raise RuntimeError("FilmCraft result exceeds the 2 MiB limit")
    try:
        return json.loads(text, object_pairs_hook=_object_pairs, parse_constant=_invalid_constant)
    except (ValueError, RecursionError) as exc:
        raise RuntimeError("FilmCraft returned malformed or ambiguous JSON text") from exc


def _dump(model):
    return model.model_dump(mode="json", by_alias=True, exclude_unset=True)


def _decode_result(response):
    """Decode one unambiguous JSON result, without dropping content blocks."""
    structured = response.structured_content
    content = response.content
    _check_size(_dump(response))
    if response.is_error:
        # Retain the complete error (including partial command-batch outcomes).
        raise RuntimeError("FilmCraft tool error: " + json.dumps(_dump(response), ensure_ascii=False))
    if not isinstance(content, list):
        raise RuntimeError("FilmCraft returned malformed content")
    if len(content) > 1 or any(block.type != "text" for block in content):
        raise RuntimeError("FilmCraft returned ambiguous or non-JSON content")
    text_value = _parse_text(content[0].text) if content else None
    if structured is not None:
        if not isinstance(structured, (dict, list)):
            raise RuntimeError("FilmCraft structured result must be a JSON object or array")
        if content and json.dumps(text_value, sort_keys=True) != json.dumps(structured, sort_keys=True):
            raise RuntimeError("FilmCraft returned conflicting structured and text results")
        return structured
    if not isinstance(text_value, (dict, list)):
        raise RuntimeError("FilmCraft result must be a JSON object or array")
    return text_value


class FilmCraft:
    """Own one official-SDK stdio session over an explicitly trusted executable.

    ``call`` returns JSON objects or arrays (``command_list`` returns an array).
    Any failed request closes the session; callers must enter a new context to
    continue. Timeouts bound each launch, initialization, listing, or tool call;
    the SDK separately bounds and shields subprocess shutdown.
    """

    def __init__(self, executable: Path, project: Path | None = None, timeout: float = 30):
        self.executable = Path(executable)
        if not self.executable.is_absolute():
            raise ValueError("FilmCraft executable must be an explicit absolute path")
        if not self.executable.is_file() or not os.access(self.executable, os.X_OK):
            raise RuntimeError(f"FilmCraft executable is not an existing executable file: {self.executable}")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("FilmCraft timeout must be a positive finite number of seconds")
        self.project = Path(project).resolve() if project is not None else None
        self.timeout = timeout
        self._stack = None
        self._session = None
        self._initialized = None
        self._pagination_params = None

    async def __aenter__(self):
        if self._stack is not None:
            raise RuntimeError("FilmCraft context is already open")
        session_type, parameters_type, stdio_client, self._pagination_params = _load_sdk()
        args = ["mcp"]
        if self.project is not None:
            args.extend(["--project", str(self.project)])
        parameters = parameters_type(command=str(self.executable), args=args, env=_environment())
        self._stack = AsyncExitStack()
        try:
            # asyncio.timeout stays in this task: wait_for would enter SDK/AnyIO
            # context managers in a different task from their eventual cleanup.
            async with asyncio.timeout(self.timeout):
                read, write = await self._stack.enter_async_context(stdio_client(parameters))
                self._session = await self._stack.enter_async_context(session_type(read, write))
            async with asyncio.timeout(self.timeout):
                self._initialized = await self._session.initialize()
        except BaseException as exc:
            await self._close()
            if isinstance(exc, TimeoutError):
                raise RuntimeError(f"FilmCraft initialization timed out after {self.timeout:g} seconds") from exc
            if isinstance(exc, Exception):
                raise RuntimeError(f"FilmCraft initialization failed: {exc}") from exc
            raise
        return self

    async def _close(self):
        stack, self._stack = self._stack, None
        self._session = None
        self._initialized = None
        if stack is not None:
            await stack.aclose()

    async def __aexit__(self, exc_type, exc, traceback):
        await self._close()

    def _require_session(self):
        if self._session is None:
            raise RuntimeError("FilmCraft must be used inside an open async context")
        return self._session

    async def call(self, tool: str, arguments: dict) -> dict | list:
        """Call the exact discovered tool; return its complete JSON container."""
        session = self._require_session()
        try:
            async with asyncio.timeout(self.timeout):
                response = await session.call_tool(tool, arguments)
                return _decode_result(response)
        except BaseException as exc:
            await self._close()
            if isinstance(exc, TimeoutError):
                raise RuntimeError(f"FilmCraft tool {tool!r} timed out after {self.timeout:g} seconds") from exc
            if isinstance(exc, Exception):
                raise RuntimeError(f"FilmCraft tool {tool!r} failed: {exc}") from exc
            raise

    async def discover(self) -> dict:
        """Read identity, every advertised tool schema, commands and document."""
        session = self._require_session()
        try:
            tools = []
            cursor = None
            seen = set()
            while True:
                async with asyncio.timeout(self.timeout):
                    page = await session.list_tools(params=self._pagination_params(cursor=cursor))
                _check_size(_dump(page))
                tools.extend(_dump(tool) for tool in page.tools)
                cursor = page.next_cursor
                if cursor is None:
                    break
                if not isinstance(cursor, str) or cursor in seen:
                    raise RuntimeError("FilmCraft tools listing returned an invalid or repeated cursor")
                seen.add(cursor)
            result = {
                "server": _dump(self._initialized.server_info),
                "initialization": _dump(self._initialized),
                "tools": tools,
                "command_list": await self.call("command_list", {}),
                "doc_inspect": await self.call("doc_inspect", {}),
            }
            _check_size(result)
            return result
        except BaseException as exc:
            await self._close()
            if isinstance(exc, TimeoutError):
                raise RuntimeError(f"FilmCraft tools listing timed out after {self.timeout:g} seconds") from exc
            if isinstance(exc, Exception):
                raise RuntimeError(f"FilmCraft discovery failed: {exc}") from exc
            raise
