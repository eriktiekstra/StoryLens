# Changelog

Entries describe capabilities and verification at that release. Later milestones supersede earlier future-work lists; historical test counts and model smokes are not current verification guarantees.

## 0.10.0 — 2026-10-20 (guided terminal projects)

- Added `storylens wizard` and made bare interactive `storylens` launch the same guided workflow. Non-terminal bare calls show help instead of waiting for input; explicit wizard requires a TTY, and existing help/version/subcommands/JSON interfaces remain independently scriptable.
- Added UI-independent persisted projects with a private local catalog, explicit technical-only/default or local-AI analysis, preflight counts/tool checks, actual progress checkpoints, cached resume and honest partial/interrupted state. Wizard projects never silently reuse the global catalog or write into the source collection.
- Added guided target-duration recommendations, source browsing/required-excluded feedback, reorder/title/hold/source-range editing, strict reload/validation and new versioned storyboard files. Edits and analysis/feedback changes invalidate earlier review/editor linkage rather than silently retaining stale approval.
- Added bounded, orientation-aware local JPEG contact sheets with metadata-free source/scene derivatives and explicit audio/unavailable labels. Scene samples disclose their own timestamps and are not a rendered movie or frame-accurate cut preview. External viewers open only on request.
- Added optional FilmCraft discovery/create/PNG-preview/MP4-export steps using existing verified APIs, separate confirmations and persisted operation reports. Native files changed after preview and changed catalog source identities cannot be exported under stale review.
- Fit visual clips to the FilmCraft sequence canvas with preserved aspect ratio before saving, fixing native-size tiny/cropped output for mixed source dimensions. Explicit void-command JSON null is supported without accepting missing payloads or hiding editor errors; multi-tool operation reports have a separate 16 MiB aggregate bound.
- Updated platform CI to `actions/checkout@v7` and `actions/setup-python@v7` (Node 24). The six-job matrix retains minimum Python 3.11 coverage and tracks the newest stable Python 3.x, checks for the latest available patch, and explicitly excludes prereleases on all three latest hosted OS images.
- Wizard preflight now detects HEIC/HEIF source files and warns when the optional `pillow-heif` decoder is missing, with the install command before analysis consent. Video/audio preflight shows the saved FFmpeg/ffprobe timeout and explains how to set a longer timeout for a new project.
- Raised the default external-tool timeout from 30 to 600 seconds for metadata, video and audio FFmpeg/ffprobe subprocesses. Explicit CLI, TOML and `STORYLENS_TIMEOUT` settings still override it; existing projects retain their saved timeout. This accommodates large/high-resolution phone media at the cost of waiting longer before an unresponsive process times out.

This stage is a terminal frontend over reusable project orchestration, not a browser GUI or HTTP server. A later browser interface can reuse project state and operations, but would need its own network authentication/origin/root policy. Private-media/NAS/GPU acceptance remains separate from generated-media/software verification.

## 0.9.0 — 2026-10-19 (M9 release and operational tooling)

- Added deterministic generated image/video/audio fixture manifests and isolated cold/warm catalog benchmarks with counters, environment, wall/process-CPU time, parent high-water RSS, optional cProfile/pstats and explicit cache/measurement caveats. Benchmarks use a new local workspace, never the user's configured catalog, and disable model inference.
- Added bounded 1..32-root durable batch submission with complete path/option preflight, atomic record creation, per-child launch failure visibility and FIFO catalog scheduling. Resume preserves job creation time and joins the tail. CLI `job batch` and typed MCP `start_jobs` expose every durable ID (26 tools total).
- Added same-user/local-cache CUDA audio inference serialization with OS-released locks and bounded waits. CPU work is unaffected; no automatic CPU fallback, tensor batching, distributed scheduling or GPU capacity claim is introduced.
- Pinned the build backend to setuptools84.0.0, explicitly included MIT LICENSE, and added wheel/sdist reproducibility, artifact integrity, sdist-to-wheel parity and isolated core/MCP install checks. Added marker/extra-aware installed dependency/license inventory with primary-source citations, explicit unknowns and native/model/container redistribution boundaries.
- Added an optional digest-pinned, nonroot Docker recipe with explicit core versions/MCP selection and documented read-only source/state/egress controls. Transitive/apt/container byte reproducibility is not claimed.
- Replaced editable-install CI with clean installed-wheel/generated-media/core-without-SDK checks followed by MCP/full regression checks across Linux/macOS/Windows and Python3.11/3.12. Corrected the observed Windows chmod assertion and worker-cleanup/process-exit race rather than skipping Windows support.
- Added operational recovery/failure guidance, including real SQLite capacity-exhaustion coverage, cache/lease/cancellation/permission boundaries and deferred acceptance gates.

A profiled synthetic local run processed 1,004 media references (197,524,218 bytes): cold catalog 17.920 seconds, warm catalog 4.336 seconds with all 1,004 files served from stage caches and no failures. Actual CLI batch preflight/FIFO execution was exercised. Core and optional-MCP containers ran offline/nonroot with read-only sources; all fixture hashes stayed unchanged. Repeated/rebuilt wheel bytes matched; sdist member contents/modes matched, while compressed sdist metadata remained variable. Installed core/MCP dependency closures were complete, with missing SPDX evidence reported explicitly. These are generated-media/software observations, not private holiday-media, NAS, GPU, driver or camera-codec acceptance; that real testing remains deferred until implementation is complete.

## 0.8.0 — 2026-10-18 (M8 optional FilmCraft interchange)

- Added optional headless FilmCraft 0.9.0 discovery through the official MCP SDK: actual server identity, complete paginated tool schemas, command registry and document inspection. No editor installation/download or core dependency is introduced; failed requests close the session and ambiguous/oversized results fail explicitly.
- Added `filmcraft create`, `preview`, `export` and saved-source `transcript` CLI actions with explicit approval and approved media roots. Creation revalidates current storyboard feedback, hashes actual selected sources, imports real editor IDs, verifies exact clip/source/frame timing and saves/reopens a new project before no-overwrite publication. Existing projects and source files are never saved in place.
- Preserved absolute nonzero MP4 presentation timing where editor duration verifies the mapping; rejected negative/unknown/rebased origins, unsupported versions and off-grid cuts instead of guessing or silently snapping. Native project schema 15 transcript import requires an explicit item/catalog identity match, validates word bounds and preserves supplied source words/speaker labels without retranscription.
- Added separate approved PNG preview and software H.264/AAC export to new files, with authorized project snapshots, online-media checks and offline-placeholder rejection. Per-operation reports retain partial outcomes; no automatic retry or transactional batch claim is made.
- Exercised the checksum-verified official Linux release with MCP 2.3, generated image/audio/video sources, save/reopen, an 8-second/192-frame sequence, real previews and movie exports. A required-image/video fixture at 24000/1001 retained [5.5, 6.501) source timing and rendered 48 frames (2.002 seconds); AAC padding made container duration 2.005 seconds. Saved source transcript import retained [5, 6) timing.

This is headless Linux/generated-media evidence, not private holiday-media/user acceptance, desktop bridge verification, cross-platform editor support or broad codec fidelity. M8's real holiday review remains pending; M9 release hardening remains open.

Verification: all 284 tests passed with the optional MCP SDK installed. Every FilmCraft action also passed from the built 0.8.0 wheel outside checkout imports. A linked-picture/audio fixture exported an audible soundtrack; all six generated source hashes remained unchanged. Approval, overwrite, source-root output, off-grid timing, current transcript constraints and a real SDK initialization timeout were exercised explicitly. Importing the installed transport without the SDK worked and launch failed with an actionable extra-install error.

## 0.7.0 — 2026-10-16 (M7 recommendations and portable storyboards)

- Added deterministic recommendations over stored evidence with separate nullable technical/relevance scores, an explicit event/timing story heuristic, named `balanced`/`technical`/`story` strategies and custom normalized weights. Optional query text explicitly reuses configured local same-space retrieval; default recommendations remain inference-free.
- Added hard duration budgets and merged request/feedback constraints: required/excluded media, mandatory include-segment unions, prohibited spans, absolute source bounds including negative/nonzero origins, and no duplicate images or overlapping selections. Conflicts, missing required sources and impossible budgets fail explicitly.
- Preserved transcript-timed conversation blocks whole across overlapping cues and gaps up to two seconds. Mandated intervals expand to cover intersecting blocks; excluded-interrupted or oversized optional blocks are skipped, never silently trimmed. This is timing continuity, not semantic comprehension or emotional-value prediction.
- Added editable portable storyboard JSON containing source IDs/times, holds, separate scores, rationale and caveats, without filesystem paths or transcript words. Strict validation checks edits against current source coverage and feedback; required/excluded corrections cannot be erased through document edits.
- Added `storyboard` and `storyboard-check PATH` CLI commands, `Analyzer.recommend`/`validate_storyboard`, MCP `recommend_storyboard`/`validate_storyboard` (25 tools total), and packaged `storyboard-v1` schema/resource. MCP filters before scoring/grouping and preserves mixed-root manual-event fail-closed behavior.
- Bounded generation to 100,000 candidates and output to 500 entries; extremely small clip targets fail before unbounded enumeration. MCP storyboards are complete documents or explicit 131,072-byte budget errors, never truncated edit plans.
- Verified generated image/audio/video CLI behavior with a 5-second video origin, a 14-second storyboard, two-cue conversation preservation, exclusion enforcement, editable title/order, invalid timing/budget rejection and unchanged source hashes. Actual MCP 2.3 SDK subprocess smoke returned 111 lossless entries and rejected unauthorized roots, mixed-root manual events and oversized documents. Both smokes also passed from the built 0.7.0 wheel outside the checkout.
- The final Linux suite passed all 259 tests with the optional MCP SDK installed and no skips, covering deterministic selection, required/excluded and source-time boundaries, conversation preservation, editable validation, feedback/source-move behavior and real stdio authorization.

Selection uses deterministic greedy whole-candidate packing, not optimal editing or calibrated storytelling judgment. No new real-model query inference, M7 cross-platform runtime, rendering or FilmCraft integration was exercised; editor integration remains M8.

## 0.6.0 — 2026-10-16 (M6 events and durable corrections)

- Added inference-free coarse event grouping from stored metadata, GPS and compatible normalized embeddings. Time is the primary join signal; embeddings may bridge a longer gap only within the same space, depth, algorithm, provider/model identity and dimension. Conservative defaults avoid over-fragmentation and enforce span/GPS bounds; explicit timezone ambiguity and undated review buckets are preserved.
- Added durable manual events with stable `manual:<uuid hex>` IDs, and independent user corrections/preferences stored in a private `<catalogstem>.feedback.sqlite3` sidecar. Feedback survives metadata/AI reanalysis, forced reruns and rescanned moved bytes. Corrections cover selection, capture time/GPS, manual event assignment and half-open source-time segments for audio/video.
- Added M6 CLI commands: `events`, `event ID`, `event-create TITLE`, `event-rename ID TITLE`, `feedback MEDIA_ID`, and `preferences`; all emit JSON. Added nine MCP tools (`list_events`, `show_event`, `create_event`, `rename_event`, `get_feedback`, `set_feedback`, `reset_feedback`, `get_preferences`, `set_preferences`) and the `storylens://schemas/curation-v1` resource, raising the typed tool count to 23.
- Added `src/storylens/schemas/curation-v1.schema.json` describing event pages, member evidence, feedback, preferences, manual events and explicit truncated-transport envelopes. Pages bind a state snapshot; stale, wrong-scope or post-change cursors are rejected.
- Added MCP fail-closed behavior: unapproved media are filtered before grouping, and manual relationships spanning approved and unapproved roots cannot disclose or mutate private titles/members. Clearing the approved source's own assignment restores automatic grouping.

## 0.5.0 — 2026-10-16 (M5 agent API and durable jobs)

- Added durable detached local scan/analyze jobs with CLI and MCP start/status/result/cancel/resume/list, persisted progress/partial failures, 2-second heartbeats and 30-second ownership leases.
- Added strict token/attempt fencing at catalog commits, one-running-job scheduling and explicit cache-preserving resume; cancellation is cooperative between work units, not an immediate model kill.
- Added optional official MCP 2.3 stdio transport with 14 typed tools, explicit startup-approved roots, pre-query authorization, bounded summaries and lossless transcript export pages. No HTTP server or protocol Tasks engine is introduced.
- Packaged the core JSON Schema in the wheel and exposed core/job/transport schema resources. Corrected audio-transcription schema_version to match the actual string "1.0" result.
- Added Codex/Claude configuration examples and operational/privacy limits. The 159-test Linux suite passes; actual SDK subprocess/CLI parity, restart/cancellation/fencing, unauthorized paths and core/MCP installed-wheel behavior were exercised.

- Windows durable workers launch through the local WMI process provider rather than inheriting the MCP host kill-on-close Job Object; shutdown survival remains covered by the real SDK reconnect test. Both platforms preserve the invoking working directory for relative configuration instead of pinning the catalog directory.
- Fixed audio-worker timeout, cancellation and output-limit cleanup on Windows; POSIX retains process-group termination.
- Install and verify FFmpeg/ffprobe on every GitHub Actions OS so the full media suite runs on Linux, macOS and Windows rather than relying on preinstalled tools. Video fixtures canonicalize temporary paths and explicitly encode HDR bitstream transfer tags for newer FFmpeg builds.

## 0.4.0 — 2026-10-15 (M4 audio/transcripts)

- Added bounded standalone audio technical analysis: signal energy, RMS dBFS, peak amplitude, digital silence, clipping fraction, silence segments and source-time provenance with stream-origin preservation.
- Added timestamped transcript import, persistence and round-trip export for SRT/VTT/JSON with optional offset/language override and independent cache keys.
- Added opt-in local audio inference adapters (faster-whisper transcription, pyannote.audio diarization, transformers sound classification) using explicitly installed model directories only; no automatic downloads, no cloud access, network-denied out-of-process worker.
- Added `transcript` CLI command, `audio_transcription_*` configuration, audio stage selection in `analyze`, schema additions for audio/transcript stages and failure envelopes.
- Provenance records model digest, runtime/library versions, device, preprocessing and privacy settings; missing optional dependencies are recorded as `None` so result-shape tests can run without installed weights.
- 125 deterministic tests pass; real FFmpeg-generated audio fixtures exercise origin offsets, clipping, silence and resampled decoded audio. CLI smoke verified transcript import and audio technical analysis.

Audio models are optional and must be installed locally; transcription quality depends on the chosen Faster-Whisper revision. Events/feedback, durable jobs/MCP and storyboards remain later milestones.

## 0.3.0 — 2026-10-15 (M3 local semantics)

- Added opt-in local CPU Transformers CLIP/SigLIP image/text embeddings from explicit installed model directories, with content/library/preprocessing identities, finite normalized vectors and no automatic downloads/remote code.
- Added bounded, metadata-free EXIF-corrected frame input and a direct loopback-only Ollama vision adapter. Installed-model capabilities/digests, daemon cloud-disabled status and runtime versions are enforced; redirects, proxies, remote aliases and model/runtime mutation fail explicitly.
- Added independent semantic_embeddings/semantic_vision caches and FAST/STANDARD/DEEP frame coverage with absolute source-time evidence. Default analysis stays inference-free; failed video prerequisites cannot authorize stale-frame semantic extraction.
- Added same-space text/image search with exact cosine top-N ranking, best-frame evidence and explicit depth/model isolation. No factual confidence, identity/event inference or story score is fabricated.
- Extended CLI, configuration, JSON Schema, architecture/privacy/setup docs and optional runtime dependencies. 78 deterministic tests pass on Linux; CPU SigLIP inference and a cloud-disabled Ollama 0.40.4/Moondream caption were exercised with real installed weights. Package installation/help and wheel build succeed.
- Fixed cross-platform catalog lookups for canonicalized filesystem paths and deferred default-database resolution until configuration validation, so explicit config works without home-directory environment variables.
- Fixed Windows Python 3.12 hashing: path-stat creation times and descriptor-stat change times are checked separately, retaining source identity and before/after mutation checks. CI now runs the entire OS/Python matrix without cancelling sibling jobs and includes scan/CLI failure details.

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
