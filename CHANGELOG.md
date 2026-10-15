# Changelog

## 0.3.0 — 2026-10-15 (M3 local semantics)

- Added opt-in local CPU Transformers CLIP/SigLIP image/text embeddings from explicit installed model directories, with content/library/preprocessing identities, finite normalized vectors and no automatic downloads/remote code.
- Added bounded, metadata-free EXIF-corrected frame input and a direct loopback-only Ollama vision adapter. Installed-model capabilities/digests, daemon cloud-disabled status and runtime versions are enforced; redirects, proxies, remote aliases and model/runtime mutation fail explicitly.
- Added independent semantic_embeddings/semantic_vision caches and FAST/STANDARD/DEEP frame coverage with absolute source-time evidence. Default analysis stays inference-free; failed video prerequisites cannot authorize stale-frame semantic extraction.
- Added same-space text/image search with exact cosine top-N ranking, best-frame evidence and explicit depth/model isolation. No factual confidence, identity/event inference or story score is fabricated.
- Extended CLI, configuration, JSON Schema, architecture/privacy/setup docs and optional runtime dependencies. 78 deterministic tests pass on Linux; CPU SigLIP inference and a cloud-disabled Ollama 0.40.4/Moondream caption were exercised with real installed weights. Package installation/help and wheel build succeed.
- Fixed cross-platform catalog lookups for canonicalized filesystem paths and deferred default-database resolution until configuration validation, so explicit config works without home-directory environment variables.

Model quality remains a separate evaluation problem: captions may hallucinate (including a border on a plain-color fixture). No universal accelerator/platform/NAS performance or multilingual accuracy claim. Transcript import/transcription, MCP/jobs, grouping/feedback and storyboards remain later milestones.

## 0.2.0 — 2026-10-15 (StoryLens / M2 technical analysis)

- Completed the StoryLens cutover across package/imports, CLI, configuration/environment, storage defaults, documentation, license notices and checkout directory. No compatibility aliases remain.
- Added independently cached image/video technical stages, all/metadata/technical selection, per-stage failures and counters.
- Added orientation-aware sharpness/blur, exposure/clipping, contrast/noise proxies and perceptual/color similarity features.
- Added paginated exact-copy groups and conservative indexed image near-duplicate candidates with disclosed recall limits.
- Added actual source-PTS video cuts, rational timing/source origin, real scene JPEGs, sampled frame proxies and first-audio-stream signal statistics.
- Added configurable scene limits/thresholds/spacing, bounded technical subprocesses, cancellation cleanup and missing-artifact regeneration without metadata invalidation.
- Extended schemas and CLI/API/privacy/performance docs. 51 tests pass on Linux; installed CLI smoke verified duplicate candidates, source immutability, two-scene JPEG output, cache reuse and schema-valid results.

Technical proxies are not semantic relevance or calibrated diagnoses. Frame coverage is sampled; HDR previews are not tone mapped. Semantic providers/search, standalone audio analysis/transcription, MCP/jobs, events/feedback, storyboards and editor integration remain later milestones.

## 0.1.0 — 2026-10-15 (M1 foundation)

- Established research-backed Python/SQLite architecture, component boundaries, independent MVP and nine working milestones.
- Added recursive read-only ingestion, exclusions, streamed SHA-256 identities, multi-path references and local SQLite persistence.
- Added Pillow image metadata and bounded external ffprobe audio/video metadata extraction with provenance and independent stage cache keys.
- Added scan/analyze/list/show/export CLI, strict TOML/environment/CLI configuration, bounded pagination and versioned JSON Schema.
- Retained per-file failures, restart-by-rerun semantics, force extraction, optional weaker stat-trust mode and newer-schema rejection.
- Added generated-media tests and real-media CLI smoke verification; fixed PNG EXIF/verification sequencing and malformed GPS bounds found during verification.
- Added MIT license, contributor/security/usage/API guides, optional HEIF dependency declaration and source-pinned FilmCraft/MCP research.

Not implemented: semantic models/embeddings/search, technical quality/scenes, audio transcription/diarization, durable async jobs/MCP, cloud providers, event grouping/feedback, storyboards or FilmCraft adapter. No unimplemented command is exposed.
