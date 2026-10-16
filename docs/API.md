# JSON contract (1.0)

The CLI and Python service run without a web server. [Packaged JSON Schema](../src/storylens/schemas/results-v1.schema.json) uses Draft 2020-12 for core result envelopes, metadata/technical/semantic/audio/transcript stages, duplicate pages, scene pages and semantic search. Extractor tags/provenance allow documented extension fields. Result schema version is independent of package version, catalog/job database user_version and algorithm version. M5 adds separate durable-job and bounded MCP envelope schemas, available through SDK resources below.

| Result | Fields |
| --- | --- |
| scan/analyze summary | schema_version, root, discovered, indexed, analyzed, cache_hits, failed, errors, errors_truncated, stages |
| list/export page | schema_version, items, next_cursor |
| show | schema_version, media |
| duplicates | schema_version, mode, items, next_cursor; near mode also search |
| scenes | schema_version, media_id, items, next_cursor, provenance |
| search | schema_version, space, depth, metric, items, limit, ranking; no cursor |
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

## Durable jobs (M5)

`storylens.jobs.JobManager(config, roots)` supplies start(path, analyze=True, stage="all", depth=None, force=False), status(id), cancel(id), resume(id), result(id) and list(limit=50, after=""). CLI `job` and MCP call the same manager. Roots are explicit existing media directories, not a tool-call override. State lives in a local catalog-adjacent `.jobs.sqlite3` database.

Job objects include schema_version="1.0", id, root, request, status, progress counters, summary/error, attempt and UTC created/updated/started/finished/heartbeat timestamps. Jobs have queued/running/cancel_requested/succeeded/failed/cancelled/interrupted states. result returns the same durable state envelope and bounded partial/final summary; media details are queried separately through catalog pages. One running job per catalog; queued workers wait without FIFO guarantees. Lease heartbeats are every 2 seconds; a missing worker becomes interrupted after its 30-second lease expires and the next job interaction observes it. Resume is explicit and increments attempt; fencing prevents an old owner publishing catalog writes after takeover.

Cancellation is persisted and checked at file/stage and catalog commit boundaries. Transport disconnection is not cancellation. Current model/decoder/hash work may finish first; no universal local-model wall-clock bound is promised. Successful stages survive cancellation/interruption; resume reuses them even after an originally forced submission.

## MCP stdio envelopes (M5)

Install the optional mcp extra, then use `serve --allow-root DIR` with trusted startup configuration. Tools are list_media, show_media, export_media, scenes, search, duplicates, import_transcript, export_transcript, start_job, job_status, cancel_job, resume_job, job_result and list_jobs. Call errors set MCP isError; invalid typed arguments are not successful empty results. The SDK publishes typed input schemas; it does not grant filesystem permission.

MCP record pages default to50 (search20), max100. show/export omit large scenes, semantic items and transcript segments with explicit counts/omission flags. Arbitrary stored metadata has a bounded response budget, individual text max16384 characters, and truncated_fields disclosure. These transport summaries are not lossless copies of full core stage payloads. Scene pages and source evidence preserve available absolute source timing; artifact paths outside approved roots are redacted.

export_transcript returns lossless serialized JSON/SRT/VTT character pages: text, total_characters and next_cursor. Concatenate text until next_cursor is null; keep format and stored transcript unchanged. Default page8192, maximum16384 characters. No arbitrary output-file writes are exposed.

Read SDK resources `storylens://schemas/results-v1` (installed core results), `storylens://schemas/jobs-v1` (durable state) and `storylens://schemas/mcp-v1` (bounded transport envelopes). The core schema ships inside the wheel, independent of checkout cwd. CLI/core schemas retain their distinct contracts.
