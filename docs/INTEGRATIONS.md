# AI providers, MCP hosts and editor integrations

## What is executable today

M1–M3 provide local indexing, technical analysis, opt-in CLIP/SigLIP retrieval and Ollama frame descriptions. Transcription, MCP, an HTTP server, storyboards and editor adapters remain unimplemented. No cloud credentials or automatic model downloads exist. The inference-free profile remains [examples/local.toml](../examples/local.toml).

## Local AI setup (M3)

Install `python -m pip install -e ".[semantic]"` in a Python 3.11+ environment. PyTorch CPU is the encoder baseline; select a trusted CPU build for your platform. The optional extra adds Transformers 4.x, safetensors, SentencePiece and protobuf. Model/runtime licenses are separate from StoryLens; do not bundle weights without checking them.

Stage a complete trusted Transformers **CLIP or SigLIP** directory explicitly: weights, config, processor and tokenizer files. Set `embedding_model` or `--embedding-model` to that directory, not a Hub name. Loading uses `local_files_only=True` and `trust_remote_code=False`, with no acquisition/fallback. A missing/incompatible runtime, incomplete directory or zero/nonfinite feature fails explicitly. CPU float32 and the slow processor are explicit for reproducibility. The provider fingerprints directory content and relevant library/preprocessing versions; moving identical files preserves embedding identity. Use immutable model snapshots. Changes during a run fail rather than storing results under the old identity.

For captions, separately install a trusted Ollama release and deliberately install a **local vision-capable model**. Configure the daemon with `OLLAMA_NO_CLOUD=1` and restart it. Bind it to loopback; the StoryLens endpoint must use a numeric loopback origin, e.g. `http://127.0.0.1:11434` (not `localhost`, a LAN host or a proxy). `/api/status` must report `cloud.disabled: true`; older daemons without this evidence are rejected. `/api/tags` must identify a uniquely installed model digest and `/api/show` must advertise vision without remote/cloud routing metadata. No pulls, redirects, proxy environment handling or cloud fallback occur in StoryLens. The daemon/model are trusted local software, not a sandbox; use OS egress controls for a stronger offline boundary.

~~~toml
[storylens]
database = "/absolute/local/catalog.sqlite3"
embedding_model = "/absolute/models/siglip"
vision_model = "moondream:latest"
ollama_endpoint = "http://127.0.0.1:11434"
semantic_timeout = 120.0
~~~

Pass that file with `--config`; opt in with `analyze PATH --depth fast|standard|deep`. `--stage semantic` defaults to STANDARD and runs only semantic stages plus the video technical dependency when applicable. FAST requires only embeddings; STANDARD/DEEP also require vision. Default `analyze PATH` remains metadata+technical, with no inference. [Depth, retrieval and limits](USAGE.md#local-semantics-and-retrieval).

## Verification and limits

Linux CPU inference was exercised with official `google/siglip-base-patch16-224` revision `7fd15f0689c79d79e38b1c2e2e2370a7bf2761ed` (Apache-2.0), Transformers 4.57.6 and PyTorch 2.14.1+cpu. Image/text features have 768 dimensions and unit norms; a direct encoder smoke denied every socket connection. Ollama 0.40.4 reported cloud disabled, advertised local vision capability and generated real captions with `moondream:latest` digest `55fc3abd386771e5b5d1bbcc732f3c3f4df6e9f9f08f1131f9cc27ba2d1eec5b` (Apache-2.0). Acquisition was a deliberate verification step into temporary directories, never an application behavior. [Pinned sources/setup](research/media-ecosystem.md).

An installed-CLI smoke analyzed three generated color images and a two-scene video, validated summary/export/search JSON against the schema, retrieved relevant color evidence for three text queries, matched a reference image to itself, and retained captions for scene representatives at source times 0.6 and 1.6 seconds. A final STANDARD rerun reused all four files' embeddings/captions and the video technical stage; source hashes stayed unchanged. Changing actual encoder-config content recomputed embeddings only and excluded old-space search rows. These small synthetic checks do not measure family-media relevance or caption truthfulness.

These are correctness checks, not a broad accuracy benchmark or throughput claim. Moondream invented a white border on a plain red fixture; captions remain `model_hypothesis` with null confidence, and must not drive factual identity/event conclusions. The short caption instruction was verified on that model; empty/malformed model output remains an error. CLIP runtime execution, accelerator performance, multilingual retrieval, diverse family-media quality and Windows/macOS inference have not been established here. Transcript import/transcription remain M4.

## Optional cloud setup plan

Future OpenAI, Anthropic, Gemini, OpenRouter and OpenAI-compatible adapters must use task-specific contracts and enforce explicit upload authorization before reading data for transmission. Configuration must include provider/model/endpoint, credential reference, data categories, timeout/concurrency/rate/retry limits and cost budget. No API names or model IDs belong in business logic. Prefer environment/keychain credential references; never export credentials. Fully local, hybrid and cloud-assisted profiles become runnable only with enforcing adapters and mocked privacy/failure tests. No automatic cloud fallback on missing local models.

## MCP setup for Codex and Claude Code

**StoryLens MCP cannot be registered yet:** there is intentionally no `storylens mcp` command. M5 will implement stdio through the maintained official Python SDK, durable application jobs, cancellation/status/result pages, schemas and path authorization. The researched current SDK is v2 with MCPServer; do not copy obsolete FastMCP/experimental Tasks examples. [MCP research](research/filmcraft-mcp.md) records exact versions and sources. No MCP protocol tests are claimed for the current release.

For now Codex or Claude Code can invoke the installed CLI with `--json` under normal shell permissions. Restrict media roots and outputs in the agent host, review disclosure of private paths/content, and do not hand untrusted agents unrestricted home-directory access. An application that performs no uploads cannot prevent a separate agent from forwarding its JSON elsewhere.

Once M5 ships, document and test the exact release executable and its stdio server with both hosts: Codex's `mcp_servers` TOML configuration and Claude Code's `mcpServers` JSON configuration. The host must point at a trusted absolute executable, local catalog and explicit allowed media roots; stdout must contain only MCP traffic. Do not publish a registration snippet pointing at a nonexistent server.

## Verified FilmCraft boundary

Public source inspected: `storytold/filmcraft`, commit `8309f21a6c3f34ddf190af7fd9bfd913053a1fd5`. There are other similarly named projects; this is a researched candidate, not proof of the user's installed version. [Pinned interface research](research/filmcraft-mcp.md) contains actual commands, tool arguments, transcript interchange formats and citations. No FilmCraft binary was installed/launched here.

Verified upstream examples (not executed): `filmcraft-cli mcp --project p.fcproj` for headless stdio; `filmcraft --control 9876` plus `filmcraft-cli mcp --bridge 127.0.0.1:9876` for a running desktop. Its desktop bridge is unauthenticated loopback TCP, not an HTTP API. Never expose it on a public interface.

Actual tools include `media_import` with absolute `paths`, `command_list`, `command_run` and `command_batch`. Discover nested command IDs/parameters from the installed version, rather than inventing timeline/export tool names. Batches are ordered, **not atomic**. Inspect document/sequence state after edits and require user review before project mutation or render/export. FilmCraft transcript interchange should be imported rather than retranscribed when available; future importers must validate actual timestamps and schema.

## Planned agent workflow (M5–M8)

1. Agent starts StoryLens analysis job, monitors bounded progress/result pages.
2. Core produces grounded scenes, transcripts, events and highlight candidates.
3. Agent requests an editor-independent storyboard with source IDs/times and selection rationale.
4. User reviews exclusions, required media, event assignments and proposed cuts.
5. Agent discovers actual FilmCraft tools/engine commands and creates/opens the approved project.
6. Agent resolves available local source paths, imports selected assets, assembles cuts and audio using discovered commands.
7. Agent inspects/renders previews, requests review, then exports through verified commands.

This is a proposed workflow, not an implemented end-to-end demonstration. Runtime tool discovery, rational time conversion, imported IDs, save/reopen and a consented rendered fixture are M8 acceptance gates. The core never depends on FilmCraft; another editor can consume the same approved storyboard.
