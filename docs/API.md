# JSON contract (1.0)

The CLI and Python service run without a web server. [JSON Schema](../schemas/results-v1.schema.json) uses Draft 2020-12 and covers result envelopes, metadata/technical/semantic stages, duplicate pages, scene pages and semantic search. Extractor tags/provenance allow documented extension fields. Result schema version is independent of package version, database user_version and algorithm version. The 0.3 package extends v1 additively with semantic stages/search; consumers should tolerate documented additions.

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

List items contain id (sha256:<digest>), kind, size and first_seen UTC timestamp. Export/show include paths, analysis keyed by metadata/image_technical/video_technical/semantic_embeddings/semantic_vision, and last failures. Path observations retain path/media_id, size, mtime_ns/ctime_ns, optional-valued birthtime and last_seen UTC. Stored references are not availability or unchanged-content guarantees.

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

Use stage="metadata"/"technical" to select inference-free subsets; use depth="fast"/"standard"/"deep" with all or semantic to opt into semantics. stage="semantic" defaults to standard and includes the applicable video technical prerequisite. app.search(text="beach", depth="fast", limit=20) or app.search(image=Path("reference.jpg"), depth="fast") requires Config.embedding_model. Configure Config.vision_model/ollama_endpoint for standard/deep captions. force=True recomputes selected stages. Service/catalog objects do not always add CLI envelopes; internal Python APIs may evolve across minor releases. Providers are concrete task-specific classes, not a dynamic plugin marketplace. No plugin ABI, HTTP endpoint or MCP server is promised yet.
