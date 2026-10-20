# Implementation plan

## Current product boundary

StoryLens retains local/NAS ingestion, metadata, cached technical/optional local-semantic stages, scenes, audio/transcripts, durable jobs/MCP, events/corrections, constrained editable storyboards and release/benchmark/deployment tooling. `storylens` / `storylens wizard` is now a loopback browser workflow: private local technical analysis, visible progress/results and an explicit handoff to the user's existing MCP host. The host owns creative decisions and calls the official FilmCraft MCP directly. StoryLens's FilmCraft CLI/adapter, native-project creation/render/export and terminal editor workflow are removed.

The browser provides a project-specific StoryLens MCP snippet using `<project>/catalog.sqlite3` plus a prompt to copy. Original-media inspection is opt-in in that prompt; cloud/provider disclosure requires separate consent. StoryLens neither launches the agent nor controls its network behavior. Private holiday-media review, real NAS/GPU performance and final hardware/media acceptance remain separate from automated/generated-media verification.

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
| M8 — historical FilmCraft adapter (removed) | Former package-owned 0.9.0 headless exchange; no current adapter/API support | Historical Linux/generated-media discovery, source timing, save/reopen, transcript and render/export results below; not verification of a user's current MCP host |
| M9 — release (implementation complete; real-environment acceptance deferred) | Collection profiler/fixture generator; bounded durable FIFO batches; shared per-user CUDA admission; pinned-backend reproducibility/install checks; license inventory; optional Docker; installed-wheel platform CI; operational failure guide | Generated collection/CLI/process/container/release checks recorded below; actual NAS/GPU/codec/private-media acceptance remains deferred |
| Browser wizard and host handoff (current) | Default `storylens` / `storylens wizard`; private project; technical analysis/preflight/resume; progress/results; copied StoryLens MCP snippet and agent prompt; separate inspection/provider consent | Current browser/authorization/handoff checks must be recorded when exercised; historical terminal/editor checks do not verify this path |

MCP follows working core capabilities rather than driving the architecture. Supplied transcript import remains independent of transcription and editors. Local inference is explicitly optional. The wizard's loopback HTTP UI is separate from the stdio MCP transport; no public/LAN service or cloud-provider adapter is introduced. The user's existing agent and official FilmCraft MCP are external systems, not future StoryLens-owned editor functionality.

## Verification policy

Use generated redistributable fixtures, not private family media. Keep behavior tests for identity/cache/privacy/partial errors and CLI integration. Use actual ffprobe and media in smoke verification. Future adapters require mock HTTP failure tests (including timeouts and budgets); MCP requires actual SDK client tests. Test disk-full and interrupted jobs when those surfaces are introduced; unavailable models/GPUs must fail explicitly, never substitute fabricated analysis. Benchmark accuracy separately from technical correctness.

## Open decisions

Model choice and revisions are configuration, not business logic. Do not promise quantitative recommendation accuracy or broad semantic/event accuracy without a labeled evaluation set. Full RAW/HEIC/HDR support depends on platform codecs and explicit fidelity tests. An optional ExifTool metadata pass may follow; do not silently install it. FilmCraft compatibility and permissions belong to the user's configured host/server, not a StoryLens adapter. Historical FilmCraft 0.9.0 checks do not certify other installations.

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

The generated-media CLI smoke indexed five image/audio/video sources, retained a video source origin of 5 seconds, produced a 14-second storyboard, preserved a two-cue conversation and honored excluded ranges/media. Edited title/order validated; out-of-source edits and an impossible required-image budget failed; all source hashes stayed unchanged. Timestamp-based conversation blocks do not establish broad meaningful-conversation accuracy. JSON is editable and portable, not a rendered film. Subsequent M8 package-adapter work is historical and has since been removed.

M7 MCP 2.3 subprocess smoke returned 111 complete entries (444 seconds), rejected private required media and mixed-root manual relationships, and rejected oversized documents rather than truncating them. Generated-media CLI and SDK smokes also passed using the installed 0.7.0 wheel outside checkout imports. New real-model query inference and cross-platform M7 runtime remain unexercised.

## Historical M8 verification (adapter removed)

This section records the removed CLI/SDK adapter, not current APIs or instructions. It formerly launched a trusted FilmCraft 0.9.0 executable and created only a new approved project from a revalidated storyboard. Full source hashes, approved roots, imported IDs, frame-grid durations, source-in/out, speed/direction and sequence identity were checked. Save/reopen preceded no-overwrite publication. Preview/export required separate approvals; native source transcript import required explicit identity mapping. Core analysis/MCP did not depend on FilmCraft. Current editor operations happen only through the user's host and its official FilmCraft MCP.

The official Linux x86_64 release archive matched its published SHA-256. MCP 2.3 discovered server `filmcraft` version `0.9.0` and 17 tools. Generated image/audio/video sources produced a reopened 8-second, 192-frame 24fps sequence and real PNG/H.264/AAC artifacts. A required-image/video fixture at 24000/1001 preserved absolute [5.5, 6.501) source timing and produced 48 video frames (2.002 seconds). Its MP4 container measured 2.005 seconds because of AAC padding; sequence/video timing is authoritative. Imported saved source words/speaker retained [5, 6) absolute timing without inference.

The former adapter rejected unsupported versions/native schemas, offline/proxy/unapproved sources, changed content, negative or unverifiable nonzero origins and off-frame cuts. Its transport bounded requests/results and closed failed sessions; it did not make exports durable or isolate the editor's OS authority. Actual private holiday-media/user review, desktop operation, macOS/Windows interoperability and large/NAS performance were not verified by these checks.

The final Linux suite passed all 284 tests. A built 0.8.0 wheel, imported outside checkout, passed discovery/create/preview/export/source-transcript smokes. A separate linked-picture/audio export produced a non-silent decoded soundtrack; all six generated source hashes stayed unchanged. Real CLI rejection checks covered approval, overwrite, source-root output, off-frame durations, newly imported conversation timing and initialization timeout. Installed transport import without the optional SDK succeeded; use reported the required extra explicitly.

## M9 implementation and acceptance boundary

Release tooling is available in 0.9.0: `benchmark generate/run`, optional pstats reports, complete-preflight 1..32-root FIFO batches through CLI/MCP, a same-user/local-cache OS CUDA mutex, installed-wheel six-target CI, pinned setuptools84.0.0 distribution checks, active marker/extra-selected license inventory and a nonroot Docker recipe. No queue broker, distributed scheduler, automatic model acquisition, GPU fallback or new cloud surface is introduced. Artifact reproducibility does not imply universal runtime/native/license certification; see [release guide](RELEASE.md) and [operational failure matrix](USAGE.md#operational-failure-matrix).

A generated local collection contained 1,004 media references (197,524,218 bytes). With cProfile enabled and default full hashing, the cold catalog pass indexed/analyzed all 1,004 in 17.920 seconds; the warm catalog pass recomputed none and reused all 1,004 in 4.336 seconds. Parent-process high-water RSS was 55,767,040 bytes; child-process memory and OS/NAS caches were not measured/controlled. This is one synthetic local-machine observation, not NAS throughput or a calibrated performance guarantee.

An actual CLI batch rejected a mixed approved/unapproved request before creating jobs, then returned all three submitted IDs and completed metadata/technical work in nonoverlapping FIFO intervals. The previous pushed Windows failures were traced to a POSIX chmod assertion and test cleanup preceding detached-worker process exit; assertions/lifecycle cleanup and the installed-wheel CI workflow were corrected. Updated remote platform results must be recorded after a run, not inferred from the fix.

Private holiday-media/user review, real NAS outage/throughput, GPU capacity/OOM/drivers, camera RAW/HEIF/HDR fidelity and final human release acceptance remain deferred. Model choice, GPU batching/performance tuning, broader codecs and other editor versions require evidence on those real environments; the implementation does not promise those outcomes.

Local container verification processed all 1,004 sources with networking disabled, a read-only root/source mount, dropped capabilities, a temporary scratch mount and a nonroot UID. Source writes were denied and all fixture hashes stayed unchanged. The optional MCP container exposed 26 tools, rejected a mixed-root batch without jobs, and completed both approved scan jobs through actual SDK stdio.

The release checker produced byte-identical repeated wheels and an identical wheel rebuilt from sdist. Sdist member bytes/modes matched, but compressed sdist bytes differed because gzip/tar metadata is outside that comparison; no byte-identical sdist claim is made. Isolated core/MCP install/schema/protocol checks passed. Source archives include release/operation scripts, guides and the optional Docker recipe.

Installed base inventory had 3 components and a complete closure with explicit SPDX evidence. Installed MCP inventory had 31 components and a complete closure; h11, mcp and mcp-types lacked valid SPDX expression metadata and remain explicit license-evidence unknowns despite separately cited upstream terms. Neither this inventory nor the Docker recipe grants native/model redistribution clearance.

## Browser workflow and handoff

The browser wizard replaces the old terminal/editor flow rather than adding an AI host or a second editor integration. It reuses private Project orchestration for preflight and technical analysis, then supplies results, a StoryLens MCP configuration snippet and a prompt for the existing agent. The agent may use analysis/storyboard tools without StoryLens running creative inference, and discovers/calls official FilmCraft tools itself.

The listener is loopback-only with private per-session authorization and origin/host checks. It must not expose arbitrary source media or be treated as a general network API. Explicit user inspection consent changes the copied prompt; provider upload requires independent approval. Analysis artifacts and private paths remain sensitive even when originals are not uploaded.

## Historical terminal workflow evidence (superseded)

The following acceptance record describes the removed terminal/editor UI, not current wizard instructions or browser verification:

Generated Linux PTY acceptance exercised both entrypoints, project resume, technical analysis of two images/two videos/one audio file, exclusion feedback, title/order/image-hold edits, contact sheets and trusted FilmCraft 0.9.0 creation/preview/export. The aspect-fitted 1920x1080 sequence retained 276 frames at 24fps (11.5 seconds); the H.264/AAC MP4 container measured 11.520 seconds with audio padding. Its decoded soundtrack retained the selected video's 660Hz tone. All five source hashes and the previous native project stayed unchanged. The integrated Python 3.12 suite passed all 352 tests; this does not replace private-media or real NAS/GPU acceptance.

