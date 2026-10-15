# Implementation plan

## MVP boundary

**M1, M2, M3 and M4 are implemented**: local/NAS ingestion, metadata, independently cached image/video technical measurements, exact/near-duplicate candidates, timestamped scenes/JPEGs, bounded frame/audio signals, opt-in local CLIP/SigLIP retrieval and evidence-linked Ollama descriptions, timestamped transcript import/export, and opt-in local faster-whisper/pyannote.audio/transformers audio inference. This is not the complete semantic product: jobs/MCP, events, feedback and storyboards remain future work. Never expose fake implementations for these.

## Milestones

| Milestone | Deliverable | Acceptance evidence |
|---|---|---|
| M1 — foundation (complete) | Python package; recursive read-only ingest/exclusions; full fingerprints; SQLite paths/media/stage cache; image + audio/video metadata; TOML/env/CLI; paginated list/show/export; docs | Synthetic image/audio/video CLI smoke; move/cache/change tests; corrupt-file isolation; source hashes unchanged; configuration and CLI tests |
| M2 — technical (complete) | Image sharpness/exposure/noise; exact/near duplicates; timestamped scene boundaries, representative frames and initial video/audio measurements | Blur/exposure/duplicate fixtures; VFR and rotation timing fixtures; no quality-based automatic discard |
| M3 — local semantics (complete) | Ollama vision and local CLIP/SigLIP embeddings; natural-language/image similarity; selected-frame descriptions; real FAST/STANDARD/DEEP coverage | Offline installed SigLIP CPU image/text smoke; real cloud-disabled Ollama/Moondream captions; depth/source-time evidence tests; same-space retrieval evaluation; real model-content cache invalidation without metadata/technical invalidation |
| M4 — audio (complete) | Import existing timestamped transcripts first; optional faster-whisper; speech/silence/quality; separate optional diarization and sound classifiers | Multilingual timing fixtures; import roundtrip; missing GPU/model/timeout behavior; no required cloud access |
| M5 — agent API | Official SDK MCP stdio; durable start/status/cancel/resume jobs; result pages, schema files; Codex/Claude setup | SDK client protocol tests; restart/cancellation and path authorization; real CLI/MCP parity smoke |
| M6 — events and feedback | Time/GPS/semantic grouping; titles with provenance; manual corrections and preferences independent of AI data | Avoid over-fragmentation evaluation; explicit timezone ambiguity; feedback survives reanalysis |
| M7 — recommendations | Separate technical/relevance/story scores; configurable strategies; editable portable storyboard with duration/required/excluded media | Source-time bounds, required/excluded invariants, meaningful conversation preservation, deterministic budget behavior |
| M8 — optional FilmCraft | Map verified FilmCraft tools to storyboard; existing transcript import; agent-mediated review/edit/export | Verify actual deployed tools first; consented end-to-end holiday workflow; no core editor dependency |
| M9 — release | Profiling, batch/GPU scheduling, packaging, optional Docker, platform CI, operational guides | Large NAS collection benchmarks; macOS/Linux/Windows and failure matrix; license audit; reproducible distribution |

MCP follows working core capabilities rather than driving the architecture. Transcript import precedes transcription to avoid duplicating editor work. Cloud providers are optional additions after capability-specific local contracts: require explicit privacy opt-in, mocked tests, timeouts/rate limits/budgets and no secret exports. HTTP is optional, not on the critical path.

## Verification policy

Use generated redistributable fixtures, not private family media. Keep behavior tests for identity/cache/privacy/partial errors and CLI integration. Use actual ffprobe and media in smoke verification. Future adapters require mock HTTP failure tests (including timeouts and budgets); MCP requires actual SDK client tests. Test disk-full and interrupted jobs when those surfaces are introduced; unavailable models/GPUs must fail explicitly, never substitute fabricated analysis. Benchmark accuracy separately from technical correctness.

## Open decisions

FilmCraft interface identity must be verified before tool mappings. Model choice and revisions are configuration, not business logic. Do not promise quantitative recommendation accuracy without a labeled evaluation set. Full RAW/HEIC/HDR support depends on platform codecs and explicit fidelity tests. An optional ExifTool metadata pass may follow M1; do not silently install it. Product-level semantic MVP is M1–M7; each milestone must work alone before proceeding.

## M2 verified boundary

Image metrics are orientation-aware bounded proxies, never relevance scores. Near-duplicate search is indexed and deliberately conservative, with disclosed recall limits. Video scenes retain actual PTS/rational timing and source origin, including VFR and rotation; representative frames are real JPEG artifacts beside the catalog. Frame defect/motion measurements are sampled, audio statistics cover the first audio stream, and HDR previews are not tone-mapped. Resource limits fail explicitly and are documented in USAGE.md. Standalone audio remains metadata-only until M4.

## M3 verified boundary

Inference is explicitly opt-in; default metadata/technical processing remains model-free. FAST embeds one scene representative, STANDARD embeds/describes up to eight, DEEP covers all detected scene representatives. Images contribute one first frame. Model files/tokenizer/processor, relevant library/runtime versions, preprocessing, depth, task, prompt/options and video dependency determine independent stage keys. Text/image search never mixes embedding spaces and reports winning source-time evidence; captions are model hypotheses with null confidence.

Real SigLIP CPU features are normalized 768-dimensional vectors and ran with all socket connections denied. Changing actual model-config content excluded old-space vectors and recomputed only embeddings while metadata/technical results stayed unchanged. Ollama 0.40.4 reported cloud disabled; the pinned local Moondream model generated evidence-linked captions. The installed CLI processed four generated read-only media files, validated JSON, retrieved relevant color/text and reference-image matches, retained two source-time caption records and reused all selected stages on rerun without source hash changes. This synthetic sanity check is not broad semantic accuracy; Moondream hallucinated a border on plain red. [Model revisions, setup and remaining limits](INTEGRATIONS.md#verification-and-limits).
