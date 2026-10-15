# JSON contract (1.0)

The CLI and Python service run without a web server. [JSON Schema](../schemas/results-v1.schema.json) uses Draft 2020-12 and covers result envelopes, metadata/technical stages, duplicate pages and scene pages. Extractor tags/provenance allow documented extension fields. Result schema version is independent of package version, database user_version and algorithm version. The 0.2 release extends v1 with technical-stage and page variants; consumers should tolerate additive documented fields.

| Result | Fields |
| --- | --- |
| scan/analyze summary | schema_version, root, discovered, indexed, analyzed, cache_hits, failed, errors, errors_truncated, stages |
| list/export page | schema_version, items, next_cursor |
| show | schema_version, media |
| duplicates | schema_version, mode, items, next_cursor; near mode also search |
| scenes | schema_version, media_id, items, next_cursor, provenance |
| Fatal handled error | schema_version, error |

## Identity, paths and stages

List items contain id (sha256:<digest>), kind, size and first_seen UTC timestamp. Export/show include paths, analysis keyed by metadata/image_technical/video_technical, and last failures. Path observations retain path/media_id, size, mtime_ns/ctime_ns, optional-valued birthtime and last_seen UTC. Stored references are not availability or unchanged-content guarantees.

Each successful stage contains cache_key, analyzed_at and result. Keys include the applicable algorithm/tool/model/parameters; media ID and stage are separate database key components. Successful results are cached independently. A failed forced rerun can leave a previous successful result alongside a newer failure; inspect both. Summary analyzed/failed file counts can overlap when different stages on the same file succeed/fail. Per-stage counts report analyzed/cache_hits/failed exactly. Cache hits count files only when every selected applicable stage is cached.

Metadata preserves EXIF/container tags, rational timing, binary base64 fields and extraction provenance. Missing fields are absent/null; timestamps without offsets remain unzoned. No semantic confidence is invented. Image technical results contain resolution, preprocessing, normalized proxy metrics, similarity features and caveats. See [methodology and limits](USAGE.md#image-technical-analysis-and-duplicates).

Video technical results contain source_timing, derivatives, scenes and frame/audio measurements. Scene records include index, start_seconds/end_seconds, start_pts/time_base, end_rational_seconds, boundary_evidence and representative_frame. The frame has path, source_timestamp_seconds, pts and time_base. Scene confidence is null: raw cut scores are evidence, not calibrated probability. Source times are absolute presentation times; retain origin when converting to editor-relative time. Preview paths refer only to derived JPEGs. [Methodology](USAGE.md#video-scenes-and-signal-measurements) documents sampling and HDR limits.

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

Use stage="metadata" or stage="technical" to select a subset, with force=True to recompute only that subset. Service/catalog objects do not always add CLI envelopes; internal Python APIs may evolve across minor releases. Stage implementations are concrete modules with provenance/cache_key/extract interfaces, not a dynamic plugin marketplace. No plugin ABI, HTTP endpoint or MCP server is promised yet.
