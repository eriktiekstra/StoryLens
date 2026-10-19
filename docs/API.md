# JSON contract (1.0)

The CLI and Python service run without a web server. [Packaged JSON Schema](../src/storylens/schemas/results-v1.schema.json) uses Draft 2020-12 for core result envelopes, metadata/technical/semantic/audio/transcript stages, duplicate pages, scene pages and semantic search. Extractor tags/provenance allow documented extension fields. Result schema version is independent of package version, catalog/job/feedback database user_version and algorithm version. Durable jobs, bounded MCP envelopes, [M6 curation](../src/storylens/schemas/curation-v1.schema.json) and [M7 storyboards](../src/storylens/schemas/storyboard-v1.schema.json) have separate schemas, available through SDK resources below.

| Result | Fields |
| --- | --- |
| scan/analyze summary | schema_version, root, discovered, indexed, analyzed, cache_hits, failed, errors, errors_truncated, stages |
| list/export page | schema_version, items, next_cursor |
| show | schema_version, media |
| duplicates | schema_version, mode, items, next_cursor; near mode also search |
| scenes | schema_version, media_id, items, next_cursor, provenance |
| search | schema_version, space, depth, metric, items, limit, ranking; no cursor |
| events | schema_version, items (event summaries), snapshot, next_cursor, provenance |
| event | schema_version, event (summary), items (member evidence), snapshot, next_cursor, provenance |
| create/rename manual event | schema_version, id, title, updated_at |
| feedback | schema_version, media_id, values, updated_at |
| preferences | schema_version, values, updated_at |
| storyboard / storyboard-check | schema_version, kind, title, duration_seconds, total_seconds, strategy, weights, required, excluded, items, warnings, provenance |
| Fatal handled error | schema_version, error |

## Identity, paths and stages

List items contain id (sha256:<digest>), kind, size and first_seen UTC timestamp. Export/show include paths, analysis keyed by metadata/image_technical/video_technical/audio_technical/semantic_embeddings/semantic_vision/audio_transcription/transcript, and last failures. Path observations retain path/media_id, size, mtime_ns/ctime_ns, optional-valued birthtime and last_seen UTC. Stored references are not availability or unchanged-content guarantees.

Each successful stage contains cache_key, analyzed_at and result. Keys include the applicable algorithm/tool/model/parameters; media ID and stage are separate database key components. Successful results are cached independently. A failed forced rerun can leave a previous successful result alongside a newer failure; inspect both. Summary analyzed/failed file counts can overlap when different stages on the same file succeed/fail. Per-stage counts report analyzed/cache_hits/failed exactly. Cache hits count files only when every selected applicable stage is cached.

Metadata preserves EXIF/container tags, rational timing, binary base64 fields and extraction provenance. Missing fields are absent/null; timestamps without offsets remain unzoned. No semantic confidence is invented. Image technical results contain resolution, preprocessing, normalized proxy metrics, similarity features and caveats. See [methodology and limits](USAGE.md#image-technical-analysis-and-duplicates).

Video technical results contain source_timing, derivatives, scenes and frame/audio measurements. Scene records include index, start_seconds/end_seconds, start_pts/time_base, end_rational_seconds, boundary_evidence and representative_frame. The frame has path, source_timestamp_seconds, pts and time_base. Scene confidence is null: raw cut scores are evidence, not calibrated probability. Source times are absolute presentation times; retain origin when converting to editor-relative time. Preview paths refer only to derived JPEGs. [Methodology](USAGE.md#video-scenes-and-signal-measurements) documents sampling and HDR limits.

Semantic results contain schema_version, provenance, items, coverage and caveats. Provenance includes algorithm, full provider identity (model content digest, libraries, preprocessing; vision also endpoint/prompt/options), space digest, depth, task, video_cache_key and selection policy. coverage contains selected_frames/available_frames. Embedding items contain evidence and an L2-normalized finite vector. Caption items contain evidence, description, interpretation=model_hypothesis and confidence=null. Image evidence is {kind:image}; video_frame evidence includes scene_index, absolute start_seconds/end_seconds and the representative_frame record with source_timestamp_seconds/pts/time_base/path. No caption confidence or emotional relevance is fabricated.

Search items contain media_id, score and winning-frame evidence. metric is cosine; score lies in [-1,1]. Search chooses the maximum frame similarity per media, descending, then lexical media ID. space binds model files/library/preprocessing identity; depth and algorithm must also match. A changed model cannot compare accidentally against old vectors; incompatible rows are excluded and same-space dimension mismatches fail. Results are a top-limit list (default 20, max 500), not a pageable snapshot. Models and source references can be historical; inspect provenance/failures before downstream use.

## Duplicate and scene pagination

Exact duplicate items contain media_id and existing indexed paths. Near items contain left_id/right_id, hamming_distance, color_distance and aspect_ratio_difference. They are suggestions, not identity proof or semantic relevance. near.search discloses BK-tree/candidate caps and conservative recall. No duplicate command modifies files. Run a fresh scan and verify current paths before acting.

List/export/exact duplicate pages order by content ID. Pass next_cursor unchanged with the same command/filter. Near cursors encode the last lexical ID pair and bind the distance parameter. Scene cursors are offsets into the current stored scene result. Pages are not frozen snapshots of concurrent catalog/parameter changes; restart pagination after reanalysis. null means finished. Default limit50, maximum500; bounds cover record count, not bytes. Export is deliberately a page, not a full-library dump.

## Events and durable corrections (M6)

`events` lists summaries; `event ID` pages members separately. An event summary contains `id`, `title`, `title_provenance`, `kind` (`automatic`, `manual`, `undated`), nullable `start_time`/`end_time`, `timezone_status`, `member_count` and `warnings`. Members contain `media_id`, `selection` and `evidence`: capture-time and GPS sources, original evidence when overridden, stored-embedding provenance or null, and the join decision with available gap/span/distance/cosine evidence. Saved segment corrections appear under `evidence.segments`. Page `provenance` contains `algorithm` (`events-1`), effective `settings`, `input_digest` and `caveats`.

Grouping considers at most 10,000 media identities with a current regular, nonsymlink source reference; overflow is an explicit error, not a truncated collection. It uses stored evidence only: no model loading, inference or network request. Close capture-time bursts stay coarse: scene cuts and visual dissimilarity do not fragment them. Defaults join adjacent comparable captures within 7,200 seconds, unless known GPS evidence contradicts the join by more than 50 km or total event span would exceed 43,200 seconds. Missing GPS is permissive. A longer gap up to 21,600 seconds can be bridged at cosine >=0.85 only when stored embeddings match space, depth, algorithm, provider/model identity and dimension. Each media contributes the L2-normalized mean of its valid stored unit vectors, not arbitrary cross-model or frame-pair comparisons. Bridges remain similarity hypotheses, not factual observations.

Offset-aware times compare after normalization to UTC; their date titles also use UTC. Naive capture times remain a separate, explicitly `timezone_ambiguous` walltime domain. No local timezone, filesystem mtime or missing midnight is inferred. Missing/invalid capture times enter `Undated media — review required`, an `undated` review bucket that asserts no factual event or member relationship. Generated date titles include source/cache-key provenance; no place or person names are invented.

Automatic IDs are `automatic-<membership SHA-256>` (undated buckets use `undated-<membership SHA-256>`); changing members changes their identity. `create_event(title)` registers a stable `manual:<uuid hex>` ID; `rename_event(event_id, title)` changes only a registered manual title. Titles must be nonblank and at most 256 characters. An empty registered event is not a grouped candidate until a current referenced member is assigned. Set member `event_id` corrections to merge chosen media into one manual event or split a subset into another; rename those events for user-supplied labels. Manual membership may cross incomparable time domains, but then the combined range is null with a warning. Reset/unassign members to return them to automatic grouping.

Feedback is an atomic partial object patch. Omitted keys stay unchanged; unknown keys and invalid values fail without a partial write:

| Key | Accepted value and meaning |
| --- | --- |
| `selection` | `neutral`, `include`, `exclude`; a review annotation, never source deletion or automatic removal from groups |
| `capture_time` | ISO8601 date-time with `T` and optional offset, or null; date-only strings are not capture evidence |
| `gps` | `{ "latitude": number, "longitude": number }` within [-90,90]/[-180,180], or null |
| `event_id` | Known registered manual ID, or null for no manual assignment; automatic IDs are not assignable |
| `segments` | At most 100 objects with `start_seconds`, `end_seconds`, `selection` (`include`/`exclude`) and optional `label` (<=256 characters) |

Explicit null `capture_time`/`gps` means **unknown**, overriding generated evidence. Reset removes the field so generated evidence applies again; it is not the same as setting null. Segment ranges are half-open absolute source intervals `[start_seconds,end_seconds)`, not zero-based editor offsets. Nonempty segments require known audio/video source coverage; finite endpoints must satisfy source origin <= start < end <= source end. Positive duration does not require a nonnegative source origin. Opposite include/exclude ranges cannot overlap; touching endpoints and same-choice overlap are allowed. Images cannot carry segments. These are selection annotations, not destructive edits.

Collection preferences are persisted patches limited to grouping settings, never provider/model paths, credentials or approved roots:

| Setting | Default | Validation |
| --- | --- | --- |
| `gap_seconds` | 7200 | finite, >0, <=604800 |
| `distance_km` | 50 | finite, >0, <=20040 |
| `semantic_gap_seconds` | 21600 | finite, >0, <=604800 |
| `semantic_similarity` | 0.85 | finite, 0..1 |
| `max_event_seconds` | 43200 | finite, >0, <=31536000 |
| `semantic_depth` | `standard` | `fast`, `standard`, `deep` |

The merged settings must satisfy `gap_seconds <= semantic_gap_seconds <= max_event_seconds`. Feedback/preferences envelopes return `values` and `updated_at` (null before any saved value); preferences always report complete effective defaults plus overrides. Corrections, manual event registry and preferences live independently in private `<catalogstem>.feedback.sqlite3` (SQLite schema 1). Generated catalog schema remains 2. Metadata/AI reanalysis, including force, preserves this sidecar. Content IDs let rescanned moved bytes retain their corrections. Back up the sidecar separately from generated analysis.

Event/member pages default to 50, core/CLI limit 1..500, MCP limit 1..100. Pass `next_cursor` unchanged for the same list or member query; null means finished. Cursors bind current effective evidence, grouping settings, feedback state and query scope. Changed state or using a list cursor for members/another event is rejected; restart pagination and refresh automatic IDs. `snapshot` is a state fingerprint, **not** a permanent frozen snapshot or a promise to serve old evidence later.


## Portable storyboard contract (M7)

`schema_version="1.0"` and `kind="storylens_storyboard"` identify a strict complete document. `duration_seconds` is the positive hard budget; `total_seconds` equals the sum of entry durations. `strategy` is `balanced`, `technical` or `story`; `weights` contains normalized nonnegative `technical`, `relevance` and `story` components summing to 1. `required`/`excluded` hold effective whole-media ID sets; mandatory segment coverage additionally comes from current feedback. `provenance` contains `algorithm="recommendations-1"` and disclosed heuristic `caveats`.

Each entry contains exactly `media_id`, `kind`, `start_seconds`, `end_seconds`, `duration_seconds`, `required`, `scores` and `reason`. Images have null endpoints and a positive hold duration. Audio/video endpoints define absolute half-open source intervals, including negative/nonzero origins; duration is end minus start. `scores` contains nullable technical/relevance, numeric story and weighted total, all available scores in [0,1]. These are proxies, not probabilities. Entries preserve playback order. No paths or transcript words are exported.

Validation checks exact shape, finite numbers, totals/budget, current source timing, effective feedback, include-segment coverage, excluded spans, duplicate images, overlapping source intervals and whole transcript-timed conversation blocks. Removing feedback constraints from edited arrays fails. Title, order, holds and intervals are editable without rewriting catalog/feedback or sources. [Scoring, packing and timing caveats](USAGE.md#recommendations-and-editable-storyboards-m7) define the complete policy and resource bounds.

## Python service

~~~python
from pathlib import Path
from storylens.config import Config
from storylens.service import Analyzer

with Analyzer(Config(Path("/absolute/local/catalog.sqlite3"))) as app:
    summary = app.scan(Path("/mounted/media"), analyze=True)
    candidates = app.duplicates(mode="near", limit=25)
    videos = app.catalog.page(limit=25, media_kind="video")
~~~

Use stage="metadata"/"technical" for inference-free subsets; use depth="fast"/"standard"/"deep" with all/semantic to opt into semantics. stage="semantic" defaults to standard and includes the video technical prerequisite. Local transcription uses stage="transcript" and configured audio_transcription_model. app.import_transcript(media_id, path, format=None, offset=0, language=None) stores supplied evidence; app.transcript(media_id, format="json"/"srt"/"vtt") exports it. force=True recomputes selected stages. No plugin ABI or HTTP endpoint is provided.

M6 methods are `app.events(limit=50, after="")`, `app.event(event_id, limit=50, after="")`, `app.create_event(title)`, `app.rename_event(event_id, title)`, `app.feedback(media_id)`, `app.set_feedback(media_id, patch)`, `app.reset_feedback(media_id, fields=None)`, `app.preferences()` and `app.set_preferences(patch)`. Reset with no fields removes all overrides; a list removes only named fields.

M7 methods are `app.recommend(duration_seconds=60, strategy="balanced", weights=None, required=None, excluded=None, image_seconds=4, clip_seconds=10, text=None, depth="standard")` and `app.validate_storyboard(document)`. Both return the complete portable document. `text` alone opts into the configured local same-space query encoder; otherwise recommendations use stored evidence only. Missing optional timing is a warning; unsatisfied mandates or resource overflow are errors. Queries operate on current regular, nonsymlink references; rescan source changes before relying on indexed identity.

## Optional FilmCraft exchange (M8)

`storylens.filmcraft_transport.FilmCraft(executable, project=None, timeout=30)` is an async context manager, imported without the optional SDK until used. `discover()` returns `server`, complete `initialization`, lossless tool definitions, `command_list` and `doc_inspect`. Paginated tools are drained, including empty-string cursors; repeated cursors, ambiguous/oversized results and tool errors fail explicitly. Requests are bounded; failed requests close the session. The adapter uses object/array JSON tools, not image-content `render_frame` results or null-returning engine commands.

`storylens.filmcraft.create(app, document, executable=..., output=..., roots=..., approved=False, fps="24", width=1920, height=1080, timeout=30)` creates a **new** FilmCraft project. `render(executable=..., project=..., output=..., roots=..., approved=False, seconds=None, timeout=300)` writes a new H.264 MP4, or PNG preview when seconds is supplied. Reports contain `schema_version="1.0"`, `status="succeeded"|"failed"`, destination, `operations` and, on failure, `error`; successful reports include inspected `sequence`. Creation also reports discovered `server` and catalog-to-editor `media` IDs. Preflight/connection errors raise; CLI emits nonzero status. Reports may include temporary paths and must not be treated as portable storyboards.

`import_transcript(app, project=..., item_id=..., media_id=..., roots=...)` imports raw saved source words from FilmCraft project schema 15, never derived edited-sequence words. The explicitly mapped local file must match the catalog content identity. The stored ordinary transcript envelope uses `format="filmcraft-project"`, algorithm `filmcraft-transcript-import-1`, original project SHA-256/name, editor item ID and zero automatic offset. Speaker indices resolve to supplied names; word ticks become absolute source seconds and are checked against source coverage. Unsupported versions, rebased/unknown origins, out-of-source words and wrong identities fail. [CLI approval, timing and output limits](USAGE.md#optional-filmcraft-interchange-m8).


## Durable jobs (M5)

`storylens.jobs.JobManager(config, roots)` supplies start(path, analyze=True, stage="all", depth=None, force=False), start_batch(paths, analyze=True, stage="all", depth=None, force=False), status(id), cancel(id), resume(id), result(id) and list(limit=50, after=""). CLI `job` and MCP call the same manager. Roots are explicit existing media directories, not a tool-call override. State lives in a local catalog-adjacent `.jobs.sqlite3` database.

Job objects include schema_version="1.0", id, root, request, status, progress counters, summary/error, attempt and UTC created/updated/started/finished/heartbeat timestamps. Jobs have queued/running/cancel_requested/succeeded/failed/cancelled/interrupted states. result returns the same durable state envelope and bounded partial/final summary; media details are queried separately through catalog pages. One running job per catalog; queued work is FIFO by enqueue timestamp (`updated_at` while queued), then ID. Resume preserves `created_at` and joins the tail with a new enqueue timestamp. Lease heartbeats are every 2 seconds; a missing worker becomes interrupted after its lease expires and the next job interaction observes it. Resume increments attempt; fencing prevents an old owner publishing catalog writes after takeover.

Cancellation is persisted and checked at file/stage and catalog commit boundaries. Transport disconnection is not cancellation. Current model/decoder/hash work may finish first; no universal local-model wall-clock bound is promised. Successful stages survive cancellation/interruption; resume reuses them even after an originally forced submission.

M9 batches accept 1..32 distinct canonical roots with one shared request. All options/paths are validated before any records or processes are created; records commit together, launches are per-job and not transactional. `start_batch` returns `{"schema_version":"1.0","items":[ordinary job documents]}` in input order. A launch failure remains a failed/resumable durable child; remaining children are launched and returned. One waiting worker exists per submitted job; this bounds a single request, not the total queue or host process count. On Windows, later pending batch records reserve up to 20 seconds per earlier WMI launch before the normal 30-second worker lease.

`start_jobs(paths, operation="scan"|"analyze", stage=..., depth=..., force=False)` is the typed MCP batch counterpart. Its compact result contains **every** child's `id`, `status`, `attempt`, plus `failed` and a details instruction; read `job_status` for full states/errors. It deliberately does not transport potentially huge repeated root/config/error documents. CLI batch returns full job documents and exit 2 if any child has already failed.

## MCP stdio envelopes (M5)

Install the optional mcp extra, then use `serve --allow-root DIR` with trusted startup configuration. The 26 tools are list_media, show_media, export_media, scenes, search, duplicates, import_transcript, export_transcript, start_job, start_jobs, job_status, cancel_job, resume_job, job_result, list_jobs, list_events, show_event, create_event, rename_event, get_feedback, set_feedback, reset_feedback, get_preferences, set_preferences, recommend_storyboard and validate_storyboard. Call errors set MCP isError; invalid typed arguments are not successful empty results. The SDK publishes typed input schemas; it does not grant filesystem permission.

The nine M6 tools mirror the service methods: `list_events(limit=50, after="")`, `show_event(event_id, limit=50, after="")`, `create_event(title)`, `rename_event(event_id, title)`, `get_feedback(media_id)`, `set_feedback(media_id, patch)`, `reset_feedback(media_id, fields=None)`, `get_preferences()` and `set_preferences(patch)`. Feedback/preferences `patch` arguments are typed JSON objects, not JSON-encoded strings; only explicitly supplied fields are patched. Nested GPS/segment objects reject unknown keys.

`recommend_storyboard` accepts the M7 service parameters with typed finite numbers, strategy/depth enums, optional weight object and required/excluded ID arrays. `validate_storyboard(document)` accepts the portable JSON object and revalidates it against approved current sources and feedback; it cannot write arbitrary output files. Both filter before scoring/grouping and retain mixed-root manual-event fail-closed behavior. Unlike metadata summaries, storyboard documents are **never truncated**: responses exceeding 131,072 serialized ASCII JSON bytes fail with a tool error. Lower the duration or use local CLI export. Full documents support up to 500 entries, including more than 100 when within the byte budget.

MCP filters unapproved media **before** grouping. A manual relationship spanning approved and unapproved sources fails closed instead of revealing a private title/member or allowing unauthorized assignment/rename. Approve all roots containing that event's assigned sources, or clear your approved media's own assignment with `event_id:null`/reset of `event_id` (or all its overrides). Preferences affect the catalog collection, not just one MCP root, but cannot alter startup provider/path configuration.

MCP record pages default to50 (search20), max100. show/export omit large scenes, semantic items and transcript segments with explicit counts/omission flags. Arbitrary stored metadata has a bounded response budget, individual text max16384 characters, and truncated_fields disclosure. These transport summaries are not lossless copies of full core stage payloads. Scene pages and source evidence preserve available absolute source timing; artifact paths outside approved roots are redacted.

The generic size bound also applies to curation evidence: `truncated:true` and `truncated_fields` explicitly identify omitted content. Do not treat such a response as complete or advance past omitted evidence; lower `limit` and repeat the **same input cursor**. A single oversized member can still need the full local CLI/core result.

export_transcript returns lossless serialized JSON/SRT/VTT character pages: text, total_characters and next_cursor. Concatenate text until next_cursor is null; keep format and stored transcript unchanged. Default page8192, maximum16384 characters. No arbitrary output-file writes are exposed.

Read SDK resources `storylens://schemas/results-v1` (installed core results), `storylens://schemas/jobs-v1` (durable state), `storylens://schemas/mcp-v1` (bounded transport envelopes), `storylens://schemas/curation-v1` (events, corrections, preferences and explicit truncated transport), and `storylens://schemas/storyboard-v1` (complete portable documents). The packaged schemas are independent of checkout cwd. CLI/core schemas retain their distinct contracts.

## Benchmark contract (M9)

`storylens.benchmark.generate(output, images=1000, videos=2, audio=2, image_size=256, seconds=2, seed=0, ffmpeg="ffmpeg", timeout=30)` creates only a new directory and returns a `storylens_benchmark_fixture` manifest with settings, environment, file-relative names, sizes and SHA-256 hashes. No private media/models are used.

`storylens.benchmark.run(config, root, workspace=..., stage="all", passes=2, profile=False)` uses a new catalog outside the source collection, never the configured catalog. Model configuration is disabled for these metadata/technical benchmarks. Its `storylens_benchmark` report contains `schema_version="1.0"`, `status`, source counts/bytes, environment, stage/stat-trust settings and completed pass records. Each record carries cold/warm **catalog** designation, wall/process-CPU seconds, parent-process high-water RSS or null, files/second, ordinary scan summary and optional pstats/hotspot data. `benchmark.json` is retained on completion/fatal interruption; partial media failures remain explicit and make CLI exit 2. OS/NAS caches are not flushed, child memory/CPU is not measured, and these measurements are not media-quality evaluations.

