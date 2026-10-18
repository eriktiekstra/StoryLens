# Implementation plan

## MVP boundary

**M1–M7 are implemented**: local/NAS ingestion, metadata, independently cached technical and local-semantic stages, duplicates, timestamped scenes/JPEGs, bounded audio analysis, transcript import/export, optional local audio inference, durable jobs, optional MCP stdio, coarse event grouping, durable manual events/corrections/preferences, and scored editable portable storyboards. M8 optional FilmCraft 0.9.0 headless exchange is implemented and generated-media verified; private holiday-media/user review remains an acceptance gate. M9 release hardening remains open.

## Milestones

| Milestone | Deliverable | Acceptance evidence |
|---|---|---|
| M1 — foundation (complete) | Python package; recursive read-only ingest/exclusions; full fingerprints; SQLite paths/media/stage cache; image + audio/video metadata; TOML/env/CLI; paginated list/show/export; docs | Synthetic image/audio/video CLI smoke; move/cache/change tests; corrupt-file isolation; source hashes unchanged; configuration and CLI tests |
| M2 — technical (complete) | Image sharpness/exposure/noise; exact/near duplicates; timestamped scene boundaries, representative frames and initial video/audio measurements | Blur/exposure/duplicate fixtures; VFR and rotation timing fixtures; no quality-based automatic discard |
| M3 — local semantics (complete) | Ollama vision and local CLIP/SigLIP embeddings; natural-language/image similarity; selected-frame descriptions; real FAST/STANDARD/DEEP coverage | Offline installed SigLIP CPU image/text smoke; real cloud-disabled Ollama/Moondream captions; depth/source-time evidence tests; same-space retrieval evaluation; real model-content cache invalidation without metadata/technical invalidation |
| M4 — audio (complete) | Import existing timestamped transcripts first; optional faster-whisper; speech/silence/quality; separate optional diarization and sound classifiers | Multilingual timing fixtures; import roundtrip; missing GPU/model/timeout behavior; no required cloud access |
| M5 — agent API (complete) | Official SDK MCP stdio; durable start/status/cancel/resume jobs; bounded result pages and packaged schemas; Codex/Claude setup | Actual SDK subprocess client; restart/cancellation/lease fencing and root authorization; CLI/MCP parity; core and MCP installed-wheel smoke |
| M6 — events and feedback (complete) | Time/GPS/semantic grouping from stored evidence; titles with provenance; durable manual events; corrections/preferences independent of AI data in private sidecar; 23 MCP tools; curation schema | Avoid over-fragmentation evaluation; explicit timezone ambiguity; feedback survives reanalysis and moved bytes; privacy fail-closed for mixed-root manual events |
| M7 — recommendations (complete) | Separate technical/relevance/story scores; configurable strategies; editable portable storyboard with duration/required/excluded media; CLI/MCP and packaged schema | Generated-media CLI smoke with nonzero source origin, mandatory/excluded intervals, whole transcript-timed conversations, deterministic budget/strategy tests, invalid-edit rejection and source hashes unchanged |
| M8 — optional FilmCraft (implemented; holiday acceptance pending) | Verified 0.9.0 headless tools; approved new-project storyboard assembly; saved source transcript import; PNG preview/H.264 export; no core editor dependency | Real SDK discovery, source hashes, imported IDs, absolute 5-second origin, exact 24000/1001 timing, save/reopen and generated-media rendering verified on Linux; private holiday-media/user review not exercised |
| M9 — release | Profiling, batch/GPU scheduling, packaging, optional Docker, platform CI, operational guides | Large NAS collection benchmarks; macOS/Linux/Windows and failure matrix; license audit; reproducible distribution |

MCP follows working core capabilities rather than driving the architecture. Transcript import precedes transcription to avoid duplicating editor work. Cloud providers are optional additions after capability-specific local contracts: require explicit privacy opt-in, mocked tests, timeouts/rate limits/budgets and no secret exports. HTTP is optional, not on the critical path.

## Verification policy

Use generated redistributable fixtures, not private family media. Keep behavior tests for identity/cache/privacy/partial errors and CLI integration. Use actual ffprobe and media in smoke verification. Future adapters require mock HTTP failure tests (including timeouts and budgets); MCP requires actual SDK client tests. Test disk-full and interrupted jobs when those surfaces are introduced; unavailable models/GPUs must fail explicitly, never substitute fabricated analysis. Benchmark accuracy separately from technical correctness.

## Open decisions

FilmCraft 0.9.0 identity and deployed tools are verified; other versions require their own compatibility evidence. Model choice and revisions are configuration, not business logic. Do not promise quantitative recommendation accuracy or broad semantic/event accuracy without a labeled evaluation set. Full RAW/HEIC/HDR support depends on platform codecs and explicit fidelity tests. An optional ExifTool metadata pass may follow M1; do not silently install it. Product-level semantic MVP is M1–M7; each milestone must work alone before proceeding.

## M2 verified boundary

Image metrics are orientation-aware bounded proxies, never relevance scores. Near-duplicate search is indexed and deliberately conservative, with disclosed recall limits. Video scenes retain actual PTS/rational timing and source origin, including VFR and rotation; representative frames are real JPEG artifacts beside the catalog. Frame defect/motion measurements are sampled, audio statistics cover the first audio stream, and HDR previews are not tone-mapped. Resource limits fail explicitly and are documented in USAGE.md. Standalone audio remains metadata-only until M4.

## M3 verified boundary

Inference is explicitly opt-in; default metadata/technical processing remains model-free. FAST embeds one scene representative, STANDARD embeds/describes up to eight, DEEP covers all detected scene representatives. Images contribute one first frame. Model files/tokenizer/processor, relevant library/runtime versions, preprocessing, depth, task, prompt/options and video dependency determine independent stage keys. Text/image search never mixes embedding spaces and reports winning source-time evidence; captions are model hypotheses with null confidence.

Real SigLIP CPU features are normalized 768-dimensional vectors and ran with all socket connections denied. Changing actual model-config content excluded old-space vectors and recomputed only embeddings while metadata/technical results stayed unchanged. Ollama 0.40.4 reported cloud disabled; the pinned local Moondream model generated evidence-linked captions. The installed CLI processed four generated read-only media files, validated JSON, retrieved relevant color/text and reference-image matches, retained two source-time caption records and reused all selected stages on rerun without source hash changes. This synthetic sanity check is not broad semantic accuracy; Moondream hallucinated a border on plain red. [Model revisions, setup and remaining limits](…

## M5 verified boundary

Official MCP2.3 stdio ClientSession/stdio_client tests exercise subprocess initialization, typed calls, schema resources, authorized query pages, durable jobs across reconnect, explicit errors and CLI parity. Detached worker tests cover submitter exit, queue continuation, cancellation, expired-lease interruption/resume, cache-preserving forced-work resume and stale-owner catalog-write fencing. Bounded transport summaries disclose omissions; serialized transcript pages are lossless. Built-wheel smoke proves core jobs work without the SDK and installed MCP resources work without checkout files. M5 Linux verification ran 159 tests successfully.

Cancellation remains stage/commit-boundary cooperative; current hashing/model work may finish first. Heartbeat leases are not a distributed multi-host scheduler. Codex/Claude launch snippets follow official host configuration, but those host applications were not launched here. At M5 no HTTP listener, cloud provider or storyboard implementation was claimed; events/feedback/corrections followed in M6 and storyboards in M7.

## M7 verified boundary

Recommendations and validation use stored evidence without inference by default. Optional query text explicitly reuses local same-space search, with relevance available for at most its top 500 matches. Technical proxies, query similarity and event/timing story heuristics stay separate; no emotional or semantic-completeness claim is made. Named/custom weights affect deterministic whole-candidate packing, not stage caches. Required media and include segments cannot disappear silently; conflicts and impossible budgets fail.

The generated-media CLI smoke indexed five image/audio/video sources, retained a video source origin of 5 seconds, produced a 14-second storyboard, preserved a two-cue conversation and honored excluded ranges/media. Edited title/order validated; out-of-source edits and an impossible required-image budget failed; all source hashes stayed unchanged. Timestamp-based conversation blocks do not establish broad meaningful-conversation accuracy. JSON is editable and portable, not a rendered film. M8 must still verify deployed FilmCraft tools and consented editor review/export.

M7 MCP 2.3 subprocess smoke returned 111 complete entries (444 seconds), rejected private required media and mixed-root manual relationships, and rejected oversized documents rather than truncating them. Generated-media CLI and SDK smokes also passed using the installed 0.7.0 wheel outside checkout imports. New real-model query inference and cross-platform M7 runtime remain unexercised.

## M8 verified boundary

The optional CLI/SDK adapter explicitly launches the trusted FilmCraft 0.9.0 executable, discovers actual tools/commands, and creates only a new approved project from a revalidated storyboard. Full source hashes, approved roots, imported IDs, frame-grid durations, source-in/out, speed/direction and sequence identity are checked. Save/reopen precedes no-overwrite publication. Preview and movie export require separate approvals; native source transcript import requires an explicit source identity mapping. No FilmCraft dependency, download, desktop bridge, arbitrary command passthrough or editor-write tool was added to core analysis/MCP.

The official Linux x86_64 release archive matched its published SHA-256. MCP 2.3 discovered server `filmcraft` version `0.9.0` and 17 tools. Generated image/audio/video sources produced a reopened 8-second, 192-frame 24fps sequence and real PNG/H.264/AAC artifacts. A required-image/video fixture at 24000/1001 preserved absolute [5.5, 6.501) source timing and produced 48 video frames (2.002 seconds). Its MP4 container measured 2.005 seconds because of AAC padding; sequence/video timing is authoritative. Imported saved source words/speaker retained [5, 6) absolute timing without inference.

Unsupported versions/native schemas, offline/proxy/unapproved sources, changed content, negative or unverifiable nonzero origins and off-frame cuts fail explicitly. The transport bounds requests/results and closes failed sessions; it does not make exports durable or isolate the editor's OS authority. Actual private holiday-media/user review, desktop operation, macOS/Windows interoperability and large/NAS performance remain unverified. M9 still needs profiling/scheduling evidence, release/failure matrix, license audit and reproducible distribution.

The final Linux suite passed all 284 tests. A built 0.8.0 wheel, imported outside checkout, passed discovery/create/preview/export/source-transcript smokes. A separate linked-picture/audio export produced a non-silent decoded soundtrack; all six generated source hashes stayed unchanged. Real CLI rejection checks covered approval, overwrite, source-root output, off-frame durations, newly imported conversation timing and initialization timeout. Installed transport import without the optional SDK succeeded; use reported the required extra explicitly.

