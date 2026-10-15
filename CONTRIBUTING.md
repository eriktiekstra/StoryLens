# Contributing

## Development

Use Python 3.11+, create a virtual environment, and run `python -m pip install -e .`. Install FFmpeg/ffprobe from a trusted OS source for integration tests. Run `python -m unittest discover -s tests -v`. No private media, model download, GPU, paid account or cloud access is needed. Generate fixtures in temporary directories; contribute only legally redistributable assets if generation cannot cover a case. Do not commit catalogs, private GPS/transcripts or credentials.

## Design rules

Read [ARCHITECTURE.md](ARCHITECTURE.md) and the [milestone plan](docs/IMPLEMENTATION_PLAN.md) before substantial changes. Keep CLI/MCP thin; implement behavior in the shared service. Use established native/ML libraries rather than reinventing codecs/models. Add concrete capability-specific interfaces only when a working implementation needs them. No placeholders advertised as features, hidden cloud fallback, automatic model acquisition, source mutations or inference telemetry.

A stage change must update its own algorithm revision and relevant parameter/model/tool cache key, never unrelated stages. Preserve feedback separately from generated results when feedback is introduced. Schema changes need explicit versions/migrations; newer databases must fail safely. Media timing must preserve PTS/time base, rotation and uncertain timezone state. Technical quality is separate from semantic/story value.

## Pull requests and verification

Describe the behavior, privacy/data flow, cache impact, dependency/model licenses and actual verification. Add deterministic tests for consumer-visible boundaries and failures. Mock cloud HTTP when providers exist; never require paid access. Use an actual CLI/media smoke in addition to tests, and record limits instead of claiming untested OS/GPU/codec support. Do not check in personal family media for reproduction.

Foundation and technical tests cover content replacement with unchanged size/mtime, cache versions/force/moves/restart, source immutability, configuration, pagination, corrupt media, missing ffprobe, timeout handling, disk-error propagation and CLI partial failures, image proxies/duplicate pagination, VFR/source-origin timing, rotation, scene JPEGs, audio statistics, artifact recovery and subprocess cancellation. FFmpeg integration skips explicitly when binaries are absent. Actual disk exhaustion, forced termination, NAS performance, GPUs, MCP and cloud adapters are future verification work, not established coverage. See [security reporting](SECURITY.md) for sensitive issues.

## Licensing

Contributions are under the repository MIT license. Pillow has its own permissive license; optional HEIF/raw/video/ML dependencies and weights have distinct licenses. FFmpeg builds may be LGPL, GPL or nonredistributable depending on options. Do not bundle arbitrary binaries/weights or assume wrapper license covers native dependencies. Track exact versions and license obligations when introducing a dependency.
