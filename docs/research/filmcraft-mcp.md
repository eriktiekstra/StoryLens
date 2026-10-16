# FilmCraft automation and MCP research

Research date: 2026-10-15. Scope: primary-source interface research and integration recommendations only; no adapter implementation.

## Executive decision

FilmCraft **does have a publicly documented MCP server, CLI and desktop control channel** in [`storytold/filmcraft`](https://github.com/storytold/filmcraft). Keep it an optional integration, not the analysis engine or the owner of StoryLens data. Prefer headless stdio for optional scripted exchange; enable the desktop bridge only when explicitly requested. The public source is enough to design an integration boundary, but an installed binary and end-to-end interoperability have **not** been verified here. [F1–F5]

The current official MCP Python SDK is **v2**, not the old `FastMCP` API. The latest stable GitHub release observed is **v2.3.0**, published 2026-10-02; its docs require Python **3.11+**. Current protocol documentation identifies **2026-07-28** as the modern revision. Crucially, v2 removed its experimental Tasks API; tasks moved to a separate extension that these SDK docs say is **not implemented yet**. Model long-running analysis as application-owned jobs exposed through ordinary tools, not as an assumed SDK Tasks feature. [M1–M4]

## Evidence levels and local discovery

- **Verified source/documentation:** facts below were read from the owning project's repository, official SDK documentation or MCP specification. This means documented/source-supported, **not runtime-tested**.
- **Verified environment discovery:** the adjacent directory listing under `/home/herdr/workspace/personal`, a descriptive content search there, and a workspace-wide FilmCraft-name glob found no FilmCraft checkout. `command -v filmcraft` and `command -v filmcraft-cli` produced no executable path (combined command exit 1). This does not establish absence outside the searched workspace or outside `PATH`.
- **Unavailable:** no installed FilmCraft version, local server configuration, live `tools/list`, exported project fixture, or operating-system-specific runtime behavior could be established. No server was launched and no media was sent anywhere.
- **Recommendation / [INFERENCE]:** proposed StoryLens design choices are separated below; they are not claims about existing FilmCraft APIs.

The FilmCraft `main` commit resolved during research to `8309f21a6c3f34ddf190af7fd9bfd913053a1fd5` (2026-10-15). Sources F1–F5 are linked to that revision to make the researched surface reproducible. The release installed by an eventual user may expose a different surface. [F0]

## Do not conflate similarly named projects

| Name | What primary sources established | Treatment |
| --- | --- | --- |
| `storytold/filmcraft` | Rust nonlinear editor with concrete automation docs, CLI configuration and MCP source. Its README associates it with ArtCraft. | The FilmCraft candidate assessed here. [F1–F5] |
| `FilmCraft-video-editing/.github` | A separate GitHub organization profile repository containing a short marketing README, `profile/` and `404.html`; the inspected repository did not provide an MCP implementation or tool schema. | Not evidence for the editor's API, and not an interchangeable download source. [N1] |
| Other video-editing MCP servers returned by search | Name/topic overlap alone does not establish ownership or compatibility. | Do not substitute their tools for FilmCraft tools. |

The intended user installation cannot be identified solely from the word “FilmCraft.” An adapter must verify executable provenance/version and discover the actual server surface before enabling writes.

## Actual FilmCraft interfaces

### Launch modes and transports

These commands are documented upstream; they were **not executed** here. [F2]

```sh
filmcraft-cli mcp --demo
filmcraft-cli mcp --project p.fcproj
filmcraft --control 9876
filmcraft-cli mcp --bridge 127.0.0.1:9876
```

- `filmcraft-cli mcp` without `--demo` or `--project` starts an empty in-process engine session; headless mode has no desktop window. `--data-dir DIR` selects user presets. [F2]
- MCP is carried over **stdio**. Bridge mode forwards to a separately running app. The desktop's control channel is **newline-delimited JSON over loopback TCP**, not an HTTP endpoint and not itself the MCP transport. `FILMCRAFT_CONTROL_PORT` is an alternative to `--control`. [F2, F3]
- Upstream `.mcp.json` registers `filmcraft` with arguments `["mcp", "--bridge", "127.0.0.1:9876"]` and `filmcraft-headless` with `["mcp", "--demo"]`, both using `target/release/filmcraft-cli`. Those relative paths assume the upstream checkout/build. [F4]
- Installed CLI locations are documented as `/usr/bin/filmcraft-cli` for Linux deb/rpm, normally `C:\Program Files\FilmCraft\filmcraft-cli.exe` for Windows MSI (not on `PATH`), and a separate CLI release ZIP on macOS. These packaging claims are upstream documentation, not cross-platform installation results. [F2]

Upstream's absolute-path host-configuration example is reproduced below. The placeholder path must be replaced by a trusted installed binary. `mcpServers` is a **host configuration shape**, not a universal MCP wire method. [F2]

```json
{
  "mcpServers": {
    "filmcraft": {
      "command": "/abs/path/filmcraft-cli",
      "args": ["mcp", "--bridge", "127.0.0.1:9876"]
    }
  }
}
```

### Verified MCP tool surface

The names and arguments below come from upstream `docs/agents.md` and `crates/automation/src/server.rs`. An adapter must still read the installed server's tool definitions; this table is not a compatibility guarantee. [F2, F5]

| Tool | Supported mode | Verified arguments / purpose |
| --- | --- | --- |
| `command_list` | Both | Optional `filter`, `enabled_only`; returns engine command IDs, labels, parameter documentation and current enabled state. |
| `command_run` | Both | Required `id`, optional `params`; executes an engine command. Nested command parameters are passed through, not invented by the MCP wrapper. |
| `command_batch` | Both | `steps` containing `{id, params?}`, optional `stop_on_error` (default true); ordered execution, each edit a separate undo step. **Not an atomic transaction**: earlier successful steps remain when a later step fails. |
| `doc_inspect` | Both | Project tree and active sequence; sequence may be null. |
| `project_inspect`, `sequence_inspect` | Both | Project/sequence state, including media/clip identifiers and sequence markers. |
| `media_import` | Both | Required `paths: array[string]` of absolute file paths; optional `bin` and `image_sequence`. The wrapper translates `image_sequence` to the engine's `imageSequence` field. |
| `render_frame`, `render_preview` | Both | Optional `seconds`, `max_side` (default 960); PNG result. Headless renders; bridge uses the Program monitor. |
| `ui_inspect`, `ui_elements` | Bridge | UI state; `ui_elements` accepts optional `prefix`. |
| `ui_click`, `ui_drag`, `ui_key`, `ui_type` | Bridge | Widget/coordinate interaction; keys/text and mouse arguments as in discovered schemas. |
| `ui_screenshot` | Bridge | Optional `panel`, `max_side` (default 1600). |
| `ui_control` | Bridge | `method` and optional `params` for documented control-channel methods. |

Two JSON resources are documented: **`filmcraft://document`** and **`filmcraft://commands`**, both `application/json`. For modern clients upstream documents `ttlMs` and `cacheScope` hints. [F2]

Verified tool **argument objects**, not standalone JSON-RPC messages, illustrate the exact wrapper spelling. Their fields are supported by F5; no output is fabricated:

```json
{"filter": "marker", "enabled_only": false}
```

Use that object with `command_list` to discover installed marker-related commands. It does **not** assert a particular marker creation/import command exists.

```json
{"id": "timeline.razor", "params": {"seconds": 3.5}}
```

That second object is upstream's `command_run` example. It mutates the active sequence, depends on appropriate project state, and is not an analysis operation. [F5]

Important result details:

- The inspected Rust wrapper's `ok_json` serializes JSON into a **text content block**. Do not assume every FilmCraft result is populated in MCP `structuredContent`; parse and validate its documented text JSON when necessary. PNG tools return image content plus text. [F5]
- Upstream documents unknown top-level tool arguments as JSON-RPC `-32602`, command failures as `isError` results, and malformed JSON as `-32700`. A tool call returning content is not by itself evidence of success. [F2]
- `command_batch` returns `completed`, `failed`, `results`; the source returns an error result if any step failed. Preserve per-step outcomes instead of retrying the entire batch blindly. [F5]
- Time is **254,016,000,000 ticks per second**; commands also accept seconds, frames or timecode where documented. IDs are obtained from inspection, not inferred from filenames or list positions. [F2, F3]

### CLI and control channel alternatives

Verified CLI examples from upstream demonstrate discovery, inspection and marker **export**, not a fabricated marker-import API. [F2]

```sh
filmcraft-cli commands razor
filmcraft-cli describe timeline.razor
filmcraft-cli --demo inspect sequence
filmcraft-cli --project p.fcproj exec markers.exportCsv path=review.csv
```

`commands` supports `--json` for machine output. The CLI's `exec` accepts JSON or `key=value` parameters; `run` accepts JSON lines of `{id, params}`. `--save` writes back to `--project`, and `--save-as` writes elsewhere. Exit statuses are documented as 0 success, 1 command failure, 2 usage error. Offline media deserves special handling: the `export`/`render` convenience commands can write an output and still exit 1, whereas `file.exportMedia` results expose `offlineMedia` for the caller to inspect. [F2]

The following is an exact upstream control-channel request, not MCP JSON-RPC: [F2]

```json
{"id":1,"method":"engine.commands"}
```

Control replies use `{id, ok, result}` or `{id, ok, error}`. The control listener rejects malformed/non-request lines and closes the connection; it limits request lines to 4 MiB and concurrent connections to 16. It has **no authentication** and should only be enabled while in use. Loopback binding and rejection of HTTP request lines reduce exposure; they do not make it an authorization boundary against other local processes. [F3]

### FilmCraft background exports

FilmCraft documents an **engine job API**, distinct from the MCP Tasks extension: [F2]

- `command_run` of `file.exportMedia` without `wait` returns `{job, path}` promptly; use engine commands `jobs.list` and `jobs.cancel` to inspect/cancel.
- With `wait: true`, export remains an in-flight tool call. A client-provided progress token enables `notifications/progress` with advancing frame counts, total frames and job status, at most every 100 ms while progress advances.
- Cancellation of the waiting call stops encoding at the next batch and deletes partial export files/sidecars; the cancelled request receives no response. The docs also describe bridge waiting behavior and concurrent inspection while encoding.
- `offlineMedia` may be non-empty even when the engine export succeeds; the caller must decide whether that is acceptable.

These are upstream behavioral claims. Job durability across process exit, restart/reconnect, released-version support and timing/cancellation behavior were not exercised. They are not a substitute for StoryLens-owned durable job state.

## Current MCP and official Python SDK

### Version and compatibility boundary

The official SDK repository/package is **`modelcontextprotocol/python-sdk` / `mcp`**. Documentation names **`MCPServer`** as the high-level v2 class, imported from `mcp.server`, and **`Client`** from `mcp`. The old `mcp.server.fastmcp.FastMCP` path is removed in v2; similarly named third-party libraries are not interchangeable evidence for this SDK. Python model fields are snake_case (`structured_content`, `next_cursor`), while wire keys retain protocol spelling. [M1–M4]

The modern 2026-07-28 protocol differs materially from 2025-11-25: no initialize handshake/session, no server-initiated JSON-RPC requests; interaction questions use multi-round-trip results and change notifications use subscription streams. `Client` defaults to `mode="auto"`, probes `server/discover` and falls back to legacy initialization when appropriate. A hard version pin alone does **not** verify compatibility or populate server identity/capabilities. Use SDK negotiation rather than copying old initialize examples or manually omitting modern request metadata. Modern wire requests require the specified `_meta` protocol/capability metadata; list results also have current result/cache requirements. [M3, M4, M8, M10]

Recommendation: when the MCP milestone is implemented, lock the supported stable `mcp` release and dependencies, record supported protocol eras, and use the official high-level SDK unless an actual requirement demands the low-level server. `mcp[cli]` is an optional development extra, not required for the analysis core. [M2, M3]

### Async work, progress and cancellation

| Concern | Verified current behavior | Consequence for StoryLens |
| --- | --- | --- |
| Async handlers | `MCPServer` awaits `async def`; plain `def` tools run in a thread. [M5] | Async transport handlers must not run CPU-bound media/model work directly on the event loop. Use application workers/processes for heavy jobs. |
| Progress | Client opts in per request with a string/integer token unique among active requests. Progress must strictly increase, total is optional, updates stop at completion; notifications are optional. Python exposes `await ctx.report_progress(...)` and per-call `progress_callback=`. [M6] | Persist job progress separately; notifications alone cannot reconstruct state after a call returns or a client disconnects. |
| Request cancellation | stdio uses `notifications/cancelled` with the request ID; modern Streamable HTTP closes its SSE response stream. Work should stop and resources be released; cancellation can race completion. [M7, M8] | Do not equate “cancel requested” with “cancelled.” Keep an explicit application job-cancel operation for work that has already returned a job identifier. |
| SDK cleanup | Async cancellation unwinds the handler; cleanup belongs in `finally`, bounded/shielded if it awaits. A synchronous handler must call `anyio.from_thread.check_cancelled()` cooperatively or it runs to completion. Modern HTTP `json_response=True` and legacy `stateless_http=True` disable propagation described by these docs. [M7] | Cancellation of a handler does not inherently kill an FFmpeg/model subprocess. The worker must stop its process tree and clean temporary artifacts according to application policy. |
| Protocol Tasks | Old experimental Tasks imports are removed; the v2 migration notes say the official Tasks extension is not implemented by this SDK yet. [M3] | Do not promise `tasks/get`, `tasks/cancel`, task subscriptions or durable execution merely because MCP is present. Use ordinary tools over application jobs; revisit the extension only after confirming implementation and host support. |

An async Python function is not itself a durable background-job system. [INFERENCE] Prefer a short job-submission call returning an application identifier, later status/result retrieval, and explicit job cancellation. A waiting-call variant can stream progress, but it must document whether cancellation aborts that underlying job. Keep job state independent of a transport connection.

### Pagination and schema design

- MCP cursor pagination covers `tools/list`, `resources/list`, `resources/templates/list` and `prompts/list`. The server chooses page size; clients return the opaque `nextCursor` unchanged until it is absent/null. **An empty string is a valid cursor**, not end-of-list. [M9]
- This does **not** automatically paginate a tool's search hits, transcript segments or analysis records. Those require an application-level bounded query/result contract. [INFERENCE from M9]
- Python `MCPServer` returns registered catalog entries in one page by default. Custom protocol list pagination requires a low-level list handler. `Client.list_*` methods accept `cursor=` and return `next_cursor`; callers must drain pages deliberately. A small static tool catalog does not need a second server abstraction merely to paginate potentially large tool-result data. [M9]
- Tool schemas default to JSON Schema **2020-12**. `inputSchema` must be a schema object; for no arguments the spec recommends an object with `additionalProperties: false`. `outputSchema` is optional, but when present structured results must conform and clients should validate them. The modern protocol permits any JSON value in `structuredContent`; applications may deliberately choose object envelopes for interoperability. [M10]
- The high-level SDK generates schemas/validates typed arguments and structured return values. Use explicit constraints and strict application models rather than accepting arbitrary dictionaries and assuming all semantically invalid fields were rejected. Schema validation does not confer filesystem authorization or enforce temporal/media semantics. [M5, M10]
- Separate JSON-RPC/protocol failure from an application/tool error (`isError`); do not return a success-shaped empty result when processing failed. Tool annotations are **hints**, untrusted unless from a trusted server, not permission enforcement. [M5, M10]

### stdio and local security

A stdio server is a normal child process, not a protocol sandbox. Official local-server security guidance notes that it may inherit credentials/environment, access files available to its user and make outbound connections. Pin/vet code and constrain privileges before launch. FilmCraft explicitly documents **no folder-confinement flags** such as `--automation-read-root`; its process can read/write wherever its user can. [M11, F2]

Recommended StoryLens policy, not a claim that MCP enforces it:

1. Local stdio by default, no listener and no cloud uploads. Any later HTTP exposure is a separate deployment/security decision, not a switch silently enabled by an editor plugin.
2. Explicit, trusted executable path plus argument vector; no shell-concatenated user input. Minimal environment; no credentials or telemetry exporters inherited without need. Do not download/run unpinned third-party packages on every tool invocation.
3. Enforce approved media/output roots within the application, resolving symlinks and rejecting traversal. For third-party FilmCraft confinement, use suitable OS/container controls; a wrapper-side path check cannot restrict the external executable's total authority.
4. Keep diagnostics on **stderr**; stdout must contain only newline-delimited MCP JSON-RPC messages, with escaped newlines inside JSON strings. Treat stderr as logs, not an automatic failure signal. [M8]
5. Treat filenames, transcripts, subtitles, metadata and external tool descriptions/results as untrusted data, never authorization or model instructions. Require deliberate approval for editor writes and output overwrite; no arbitrary shell execution tool.
6. Bound request/result sizes, concurrency and job outputs. Persist private results locally; return references and small pages rather than injecting entire transcripts or base64 media by default.
7. Closing stdin is the portable graceful-shutdown signal. Arrange bounded worker cleanup and OS-appropriate process-tree termination. Do not automatically retry non-idempotent imports/edits after ambiguous disconnects. [M8]

## Proposed portable integration contract — not an existing API

This recommendation deliberately specifies responsibilities, not invented FilmCraft tools or an adapter implementation.

**Ownership.** StoryLens owns source identity, analysis jobs, artifact manifests, transcripts, observations and provenance. CLI, future MCP and optional editor adapters call the same application services. No core package imports FilmCraft, requires an editor project, or depends on a running MCP host. The entire library remains useful when FilmCraft is absent.

**Media references.** Use stable application asset identifiers plus user-authorized local locations and fingerprints; do not treat a path string as content identity. Resolve editor media to these assets explicitly. A portable manifest should not depend on one machine's absolute paths; a local adapter supplies the authorized path mapping. Artifacts stay local unless an explicit export/upload action is chosen.

**Time.** Preserve rational start/duration or integer ticks with an explicit timescale, source/sequence time domain and half-open range semantics. Include frame-rate numerator/denominator where relevant; do not use rounded floating seconds as the canonical join key. FilmCraft's ticks are an adapter conversion, not the domain's required timebase. Interpret source in/out, timeline offset, speed/reverse and frame snapping explicitly; arbitrary edited-sequence-to-source mapping must not be assumed. [F2, F5]

**Results and provenance.** Version the application schema. Associate observations/segments with asset identity, time range, storylens/model version, settings, confidence where meaningful, and references to persisted artifacts. Keep warnings, cancellation and partial/error outcomes distinguishable from completed results. FilmCraft IDs belong in optional adapter linkage, not portable analysis identity.

**Jobs.** Give submitted work an application job identifier and observable lifecycle (queued, running, cancellation requested, succeeded, failed, cancelled), progress with optional totals, terminal error/warnings and artifact references. Define restart/interruption behavior in the application. Submission/status/result/cancel semantics must be usable identically through CLI and MCP; these are proposed application operations, not names claimed to exist in FilmCraft or MCP core.

**Queries.** Use bounded, deterministic pages and opaque cursors tied to query/snapshot semantics for long transcripts/observations. Publish a schema and versioned result shape; avoid forcing consumers to scrape human summaries. This is application data pagination, distinct from protocol catalog pagination.

**Editor boundary.** Expose optional capabilities for reading editor state and delivering reviewed interchange/annotations. Discover the real editor command registry and map capabilities only after checking semantics. Keep automatic timeline mutation out of the analysis path. Require explicit target project/sequence/media mapping, a saved copy or deliberate save policy, write approval and a per-operation report. `command_batch` is not transactional, and no currently verified tool should be advertised as a dedicated “analyze media,” “import findings,” or “create review markers” tool. [F2, F5]

**Interchange before coupling.** Start with documented editor-independent analysis artifacts and reviewed exports. FilmCraft's verified marker CSV operation is an **export**, not proof it can import the same CSV. Any marker/transcript/interchange import needs its own command/schema and round-trip evidence before it becomes an adapter capability. Keep future adapters separate and optional rather than baking `.fcproj`, UI coordinates or FilmCraft ticks into application contracts.

## Remaining evidence gates, not foundation blockers

| Missing evidence | Why it matters | Evidence needed before optional adapter acceptance |
| --- | --- | --- |
| Intended FilmCraft distribution and installed version | Similarly named projects and rapidly changing `main` are not a deployment contract. | Trusted executable/version/release provenance and captured actual tool/resource/command schemas. |
| Live Python-SDK-to-FilmCraft connection | Source docs establish surface, not interoperable runtime behavior. | stdio negotiation, paged discovery handling, read-only inspect and real error result capture. |
| Findings/marker import command and target semantics | No dedicated analysis/findings import API was verified. | Actual command registry entry and parameter schema; fixture exercising nonzero source in, timeline offset, non-integer FPS, speed/reverse and multiple sequences as supported. |
| Save/undo and partial mutation policy | `command_batch` is sequential, not atomic. | Saved-copy fixture, per-step failure report and checked project state after cancellation/failure. |
| Restart and cancellation guarantees | Request cancellation and engine jobs do not establish durable analysis jobs. | Worker subprocess cleanup, restart recovery and cancellation race evidence; no orphaned partial artifacts. |
| Cross-platform runtime and confinement | Documentation names Windows/Linux/macOS packaging, not tested behavior. | Installation/path/stdio and approved-root/process-tree tests on each supported OS. |

These gaps do **not** prevent the editor-independent analysis foundation. They prevent claiming a production FilmCraft adapter or promising unverified commands today. No adapter, implementation code, build, test or smoke run was produced/executed for this research assignment.

## Primary sources

FilmCraft source snapshot:

- **F0** — [GitHub main commit](https://github.com/storytold/filmcraft/commit/8309f21a6c3f34ddf190af7fd9bfd913053a1fd5); resolved through the repository's commits API during research.
- **F1** — [FilmCraft README](https://github.com/storytold/filmcraft/blob/8309f21a6c3f34ddf190af7fd9bfd913053a1fd5/README.md).
- **F2** — [Agent usage, tools, CLI, export progress/cancellation and security limitations](https://github.com/storytold/filmcraft/blob/8309f21a6c3f34ddf190af7fd9bfd913053a1fd5/docs/agents.md).
- **F3** — [Control protocol and MCP reference](https://github.com/storytold/filmcraft/blob/8309f21a6c3f34ddf190af7fd9bfd913053a1fd5/docs/control-protocol.md).
- **F4** — [Repository MCP configuration](https://github.com/storytold/filmcraft/blob/8309f21a6c3f34ddf190af7fd9bfd913053a1fd5/.mcp.json).
- **F5** — [Actual Rust MCP wrapper, argument structs, results and tool handlers](https://github.com/storytold/filmcraft/blob/8309f21a6c3f34ddf190af7fd9bfd913053a1fd5/crates/automation/src/server.rs), especially `ListParams`, `RunParams`, `BatchParams`, `MediaImportParams`, `ok_json`, `command_batch` and `render_frame`.
- **N1** — [Separate FilmCraft-video-editing profile repository](https://github.com/FilmCraft-video-editing/.github).

MCP / official Python SDK, accessed on the research date:

- **M1** — [Official SDK stable v2.3.0 release](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.3.0), also [GitHub latest-release API](https://api.github.com/repos/modelcontextprotocol/python-sdk/releases/latest).
- **M2** — [Official SDK installation / Python 3.11+ / optional extras](https://py.sdk.modelcontextprotocol.io/get-started/installation/).
- **M3** — [What's new in v2: class renames, protocol changes, removed experimental Tasks](https://py.sdk.modelcontextprotocol.io/whats-new/); includes the link to [SEP-2663](https://github.com/modelcontextprotocol/modelcontextprotocol/pull/2663). Extension availability here is reported from the SDK's own limitation, not inferred from a proposal title.
- **M4** — [SDK protocol version negotiation and modes](https://py.sdk.modelcontextprotocol.io/protocol-versions/index.md).
- **M5** — [High-level Python tools, validation and handler execution](https://py.sdk.modelcontextprotocol.io/servers/tools/index.md); [structured output](https://py.sdk.modelcontextprotocol.io/servers/structured-output/index.md).
- **M6** — [2026-07-28 progress specification](https://modelcontextprotocol.io/specification/2026-07-28/basic/patterns/progress); [SDK progress API](https://py.sdk.modelcontextprotocol.io/handlers/progress/).
- **M7** — [2026-07-28 cancellation specification](https://modelcontextprotocol.io/specification/2026-07-28/basic/patterns/cancellation); [SDK cancellation and cleanup limitations](https://py.sdk.modelcontextprotocol.io/handlers/cancellation/).
- **M8** — [2026-07-28 stdio transport, framing, metadata, shutdown and compatibility](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio).
- **M9** — [2026-07-28 pagination specification](https://modelcontextprotocol.io/specification/2026-07-28/server/utilities/pagination); [SDK pagination defaults and low-level customization](https://py.sdk.modelcontextprotocol.io/advanced/pagination/index.md).
- **M10** — [2026-07-28 tools, JSON Schema, structured results and security](https://modelcontextprotocol.io/specification/2026-07-28/server/tools); [base protocol and modern metadata](https://modelcontextprotocol.io/specification/2026-07-28/basic/index).
- **M11** — [Official local server security guidance and stdio trust model](https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/local-server-security).

## M5 implementation evidence addendum — 2026-10-16

Primary-source review only; no server, client, build or test was run for this addendum. Earlier research is preserved above. **Correction:** both current **mcp 2.3.0** and maintenance **mcp 1.30.0** declare **Python >=3.10**, superseding the earlier Python 3.11+ statement. Sources: [current PyPI metadata](https://pypi.org/pypi/mcp/json), [v1.30.0 metadata](https://pypi.org/pypi/mcp/1.30.0/json), and [v2.3.0 pyproject](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/pyproject.toml).

### Dependency and high-level APIs

**Recommendation / [INFERENCE]:** use optional dependency **`mcp>=2.3,<3`**, without the CLI extra, for new StoryLens code. The official SDK's FastMCP API is the **v1 maintenance line**, not the current stable interface; do not confuse it with the separately distributed fastmcp package. The [official v1 README](https://github.com/modelcontextprotocol/python-sdk/blob/v1.x/README.md) identifies v2 as stable and promises v1 critical/security fixes. If deliberately targeting FastMCP, use **mcp>=1.30,<2** instead.

| Surface | Maintained v1 FastMCP | Current v2.3.0 |
| --- | --- | --- |
| Server import | `from mcp.server.fastmcp import FastMCP` | `from mcp.server import MCPServer` |
| Construction | `FastMCP("StoryLens")` | `MCPServer("StoryLens")` |
| Tool registration | `@server.tool()` | Same decorator; optional `structured_output=False` |
| Explicit result | `CallToolResult(content=[TextContent(type="text", text=message)], isError=True)` | Use `is_error=True` and `structured_content=payload` |
| Resource registration | `@server.resource(uri, mime_type="application/json")` | Same |
| Stdio entry point | `server.run(transport="stdio")` | Same synchronous entry point |
| Async entry point | `await server.run_stdio_async()` | Same |

Both result classes import from `mcp.types`. Returning a `CallToolResult` directly preserves application control of content and the tool-error flag; a domain failure should not be disguised as successful text. `ToolError` and ordinary handler exceptions are converted to tool-error results by the high-level server. URI `{job_id}` placeholders register resource templates and become handler arguments. For JSON resources, return serialized JSON text (`str`); binary resources return `bytes`. Sources: [v1 tools/errors/resources documentation](https://github.com/modelcontextprotocol/python-sdk/blob/v1.x/docs/server.md), [v1 server signatures](https://github.com/modelcontextprotocol/python-sdk/blob/v1.x/src/mcp/server/fastmcp/server.py), [v2.3.0 server signatures/error handling](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/mcpserver/server.py).

V2 removes the `mcp.server.fastmcp` import path and uses snake_case Python attributes, including `is_error`, `structured_content`, `input_schema`, `resource_templates` and `mime_type`. MCP wire aliases remain camelCase; use `model_dump(by_alias=True)` for explicit protocol serialization. Synchronous v2 handlers run on worker threads and may execute concurrently; `async def` handlers remain on the event loop. Source: [tagged migration guide](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/migration.md).

### Official ClientSession/stdio integration

Explicit transport/session integration remains available in v2; a hand-written JSON-RPC client is unnecessary:

```python
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Inside an async function; executable, arguments and URI come from the caller.
params = StdioServerParameters(command=executable, args=server_args)
async with stdio_client(params) as (read, write):
    async with ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
        resources = await session.list_resources()
        templates = await session.list_resource_templates()
        result = await session.call_tool(tool_name, arguments)
        # V2 attributes: result.is_error, result.structured_content
        resource = await session.read_resource(resource_uri_string)
```

`StdioServerParameters` accepts command, args, env and cwd. `stdio_client(params, errlog=sys.stderr)` spawns the subprocess and yields the read/write pair; the context manager owns shutdown. Explicit env entries merge over a minimal inherited allow-list, not the whole parent environment. V2's simpler high-level alternative is `async with Client(params) as client`, which initializes automatically. Sources: [official nested-client example](https://github.com/modelcontextprotocol/python-sdk/blob/v1.x/docs/client.md), [v2 exports](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/__init__.py), [v2 stdio transport](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/client/stdio.py), [v2 session signatures](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/client/session.py), [official transport guide](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/client/transports.md).

### Official Codex and Claude Code stdio configuration

The following **proposed StoryLens examples / [INFERENCE]** adapt the official host syntax to the agreed CLI. Replace absolute paths with trusted installed locations; these commands were not executed.

**Codex:** configuration lives in `~/.codex/config.toml` or trusted-project `.codex/config.toml`. Each server uses a `[mcp_servers.NAME]` table with required command and optional args, cwd, env and env_vars. CLI equivalent: `codex mcp add storylens -- /absolute/path/to/storylens serve --allow-root /absolute/path/to/media`. Source: [OpenAI Codex MCP docs](https://developers.openai.com/codex/mcp).

```toml
[mcp_servers.storylens]
command = "/absolute/path/to/storylens"
args = ["serve", "--allow-root", "/absolute/path/to/media"]
```

**Claude Code:** `claude mcp add --transport stdio --scope project storylens -- /absolute/path/to/storylens serve --allow-root /absolute/path/to/media`. The `--` separator ends host options. Project scope writes `.mcp.json`; default local scope and user scope use `~/.claude.json` with different visibility. Source: [Anthropic Claude Code MCP docs: local stdio and configuration scopes](https://code.claude.com/docs/en/mcp).

```json
{
  "mcpServers": {
    "storylens": {
      "type": "stdio",
      "command": "/absolute/path/to/storylens",
      "args": ["serve", "--allow-root", "/absolute/path/to/media"]
    }
  }
}
```
