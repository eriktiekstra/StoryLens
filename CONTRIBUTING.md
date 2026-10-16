# Contributing

## Development

Use Python3.11+, create a virtual environment and run `python -m pip install -e ".[mcp]"` for the full integration suite (or install the base package for core-only work). Install trusted FFmpeg/ffprobe. Run `python -m unittest discover -s tests -v`. No private media, model download, GPU, paid account or cloud access is needed. Generate fixtures in temporary directories; contribute only legally redistributable assets. Do not commit catalogs, private GPS/transcripts or credentials.

For real semantic verification, install the optional semantic extra and stage trusted local weights deliberately. No test-suite model acquisition is allowed. Run a CPU offline encoder and a loopback cloud-disabled Ollama smoke separately from deterministic tests; record model/runtime digests, retrieval relevance judgments and hallucinations rather than claiming broad accuracy.

## Design rules

Read [ARCHITECTURE.md](ARCHITECTURE.md) and the [milestone plan](docs/IMPLEMENTATION_PLAN.md) before substantial changes. Keep CLI/MCP thin; implement behavior in the shared service. Use established native/ML libraries rather than reinventing codecs/models. Add concrete capability-specific interfaces only when a working implementation needs them. No placeholders advertised as features, hidden cloud fallback, automatic model acquisition, source mutations or inference telemetry.

A stage change must update its own algorithm revision and relevant parameter/model/tool cache key, never unrelated stages. Preserve feedback separately from generated results when feedback is introduced. Schema changes need explicit versions/migrations; newer databases must fail safely. Media timing must preserve PTS/time base, rotation and uncertain timezone state. Technical quality is separate from semantic/story value.

## Pull requests and verification

Describe the behavior, privacy/data flow, cache impact, dependency/model licenses and actual verification. Add deterministic tests for consumer-visible boundaries and failures. Mock cloud HTTP when providers exist; never require paid access. Use an actual CLI/media smoke in addition to tests, and record limits instead of claiming untested OS/GPU/codec support. Do not check in personal family media for reproduction.

The suite covers identity/cache/error/technical/semantic/audio behavior, real official-SDK stdio calls, detached submitter exit, queue continuation, cancellation, expired-lease recovery, stale-owner write fencing, authorized catalog filtering and M6 event/feedback boundaries. GitHub Actions installs FFmpeg and the MCP extra on Linux/macOS/Windows for Python3.11/3.12. Base installs remain usable without MCP; MCP tests skip explicitly if that optional extra is absent. Real disk exhaustion, multi-host NAS scheduling, universal GPU performance, broad family-media event accuracy and cloud/editor adapters remain unverified.

## Licensing

Contributions are under the repository MIT license. Pillow has its own permissive license; optional HEIF/raw/video/ML dependencies and weights have distinct licenses. FFmpeg builds may be LGPL, GPL or nonredistributable depending on options. Do not bundle arbitrary binaries/weights or assume wrapper license covers native dependencies. Track exact versions and license obligations when introducing a dependency.
