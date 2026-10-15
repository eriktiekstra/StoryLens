# AI providers, MCP hosts and editor integrations

## What is executable today

M1/M2 provide local metadata indexing and deterministic technical analysis. No AI provider, embedding search, transcription, MCP server, HTTP server, storyboard command or editor adapter is shipped. Calling a model name a configuration profile would not create those capabilities. There are no cloud credentials to configure and no automatic model downloads.

A fully local foundation profile is [examples/local.toml](../examples/local.toml): run `storylens --config examples/local.toml analyze /mounted/media`. Hybrid and cloud-assisted profiles are architectural plans, not accepted configuration keys today.

## Local AI setup plan (M3/M4)

[Primary-source ecosystem research](research/media-ecosystem.md) evaluates Ollama, llama.cpp, MLX, CLIP/SigLIP, faster-whisper, pyannote, OpenCV and PySceneDetect, with capabilities and licenses separated. Install/download models only deliberately, from trusted sources with accepted licenses. Different tasks need different models: text/image encoders for retrieval, a vision-capable model for captions, Whisper for transcription, a separate diarizer for speaker turns. Import existing transcripts before scheduling new inference.

For a future Ollama adapter, upstream now has cloud features: use its documented `OLLAMA_NO_CLOUD=1`/local-only mode, an explicitly local model and loopback binding. A local endpoint alone is not proof of private inference. CPU is the baseline where a selected model supports it; CUDA/Metal/other acceleration is optional and must be verified per runtime/model/platform. No hardware throughput or memory floor is established here.

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
