# StoryLens architecture

Status: M1–M5 implemented: foundation, technical analysis, local semantics, audio/transcripts and durable agent API. Events, feedback and storyboards remain planned.

## Scope and decisions

StoryLens is an editor-independent, local-first media intelligence library with thin CLI and optional MCP transports. It is not an editor or an autonomous agent. Working milestones M1–M5 cover ingestion, metadata, technical measurements, duplicates, timestamped scenes, local semantics, audio/transcripts and durable jobs; events/feedback/storyboard requirements remain in the roadmap.

Use Python 3.11+, SQLite from the standard library, Pillow/NumPy for image metadata and bounded technical measurements, and external ffprobe/FFmpeg for audio/video metadata and technical processing. Python has mature bindings to native media and ML libraries. Rust offers strong resource control but would require a second ML runtime or service. TypeScript is attractive for MCP but a weaker fit for local ML. A hybrid adds packaging, IPC and versioning costs before a measured bottleneck exists. Profile before adding another language.

No server, queue broker, vector database, Docker requirement, model download or cloud account is needed for M1/M2. MIT covers original code; distributed dependencies and model weights retain their licenses. Do not bundle an arbitrary FFmpeg build: GPL/nonfree options change redistribution obligations.

## Boundaries

- **Configuration**: TOML → environment → explicit CLI options. Only implemented settings are accepted; misspellings fail.
- **Ingestion**: recursive traversal, exclusions, regular files only, streamed fingerprints, source stability checks. Content identity is independent of paths.
- **Metadata**: read-only Pillow and bounded ffprobe subprocesses. Preserve raw EXIF/ICC/XMP and ffprobe JSON alongside normalized fields. Do not infer a timezone from an EXIF timestamp lacking an offset. Filesystem mtime is not capture time.
- **Storage**: local SQLite transactions, schema version gate, indexed content identities and paths, stage-keyed successful results. Failures remain explicit and retryable.
- **Service**: orchestrates ingestion and metadata without depending on argparse, MCP, editors, or a cloud provider.
- **Technical stages**: image_technical (bounded, orientation-aware proxies and similarity features) and video_technical (source-PTS cuts, derived JPEGs, sampled frame proxies and first-stream audio statistics), independently cached from metadata.
- **Semantic providers/stages**: optional local CPU Transformers CLIP/SigLIP image/text encoders and an explicitly loopback, cloud-disabled Ollama vision daemon. Embedding and caption stages cache independently from each other and from technical results. Search performs exact cosine ranking only within one model/content/library/preprocessing space and depth.
- **CLI/MCP**: synchronous CLI commands plus durable job controls; the optional stdio SDK transport calls the same services and adds explicit approved-root authorization and bounded envelopes.

Future modules depend inward on the service and storage contracts. Add concrete modules when needed, not a dynamic plugin marketplace.

## Identity and cache invariants

Media ID is sha256:<full-file-digest>. Identical bytes share analysis; each observed absolute path has its own filesystem metadata. A move adds a path to the same identity, and missing old paths remain historical references, not deleted media. Path availability must be checked at use time. A changed path points at the new content identity; previous content results remain retained.

Every stage key covers content identity, stage name, algorithm version, tool/model revision, relevant parameters, depth and segment scope when applicable. M1 metadata includes Pillow or ffprobe version. Changing another stage must not invalidate metadata. Force recomputes the selected stage. Failed extraction never becomes a successful cache hit. Persist each successful file before proceeding; interruption resumes by rerunning the same command.

Default scans hash every supported file in bounded chunks. This is I/O-expensive on a NAS but detects same-size, same-mtime replacements. Optional stat trust reuses an identity only on matching size and nanosecond mtime; it is explicitly weaker, never the default. Detect changes during hashing/extraction using stat snapshots; concurrent adversarial writes cannot be ruled out without a filesystem snapshot. Use quiescent libraries or NAS snapshots for consistency.

SQLite stays on local storage; sources may be read-only SMB/NFS mounts. WAL requires shared-memory coordination and is inappropriate across machines on a network filesystem: https://sqlite.org/wal.html . Use short per-file transactions and a bounded busy timeout. Disk-full/storage errors abort the operation rather than pretending analysis succeeded. No source files or sidecars are written.

## Timestamp and media semantics

Store filesystem nanosecond timestamps separately from embedded timestamps. Preserve unparsed creation_time and EXIF DateTimeOriginal/offset tags; M1 does not guess capture chronology. Preserve rational frame rates, time_base, start_time, rotation side data, audio tracks, color primaries/transfer and mastering metadata from ffprobe. Average frame rate is not a timeline: future scenes use presentation timestamps in source time base, not frame_number / fps. Normalize rotation and tone-map HDR only in derived analysis artifacts, never originals; report those transforms in provenance. HEIF optional decoder support is distinct from HDR fidelity. Current M2 JPEG previews are simple 8-bit conversions with no tone mapping; this limit is explicit in result provenance. RAW metadata requires optional ExifTool; M1 reports unsupported image decoding explicitly instead of claiming full RAW support.

## Technical implementation

Stage selection is all/metadata/technical/semantic. Default all remains inference-free; depth explicitly opts into semantic stages (semantic-only defaults to standard). Failures isolate by stage; cache identity includes dependencies and relevant parameters. Missing scene JPEGs regenerate the video dependency. Derived JPEGs live under the catalog-adjacent .artifacts tree; current-run artifacts are excluded from ingestion and failed extraction cleans only its own partial files. Old/forced artifacts have no automatic garbage collection. Exact duplicate queries use content identities and existing path observations; near candidates use a bounded pHash BK-tree, color/aspect checks and disclosed recall limits. Nothing deletes sources or ranks emotional significance. See USAGE.md for limits and timing/measurement semantics.

## Local semantic implementation (M3)

LocalEmbeddings exposes identity/image/text; OllamaVision exposes identity/describe. Neither loads/downloads a model on import. Local encoders require an explicit installed model directory, use CPU float32, local_files_only and no remote code, and fingerprint model/tokenizer/processor content plus relevant libraries and preprocessing. Frame input is EXIF-corrected, bounded metadata-free RGB. Ollama uses direct numeric-loopback HTTP, no proxy or redirects, bounded responses/timeouts, disabled-cloud status evidence and inspected installed vision-capable models. Model digests and daemon versions are pinned through generation; mutation discards results. Model acquisition remains separate and deliberate.

FAST embeds the middle scene representative without vision; STANDARD embeds/describes up to eight uniformly spaced scene indices; DEEP embeds/describes every detected scene representative. Images contribute one first frame. Video depends on successful current technical results/JPEGs, preserving absolute PTS-based evidence. Each semantic stage publishes atomically across its selected frames and reports coverage; successful sibling stages survive failure/interruption. No per-frame durable jobs or universal encoder timeout is claimed.

Retrieval scans compatible normalized vectors and returns bounded top-N media by maximum frame cosine, with ID tie breaking and winning evidence. No cross-space comparison, caption-keyword fallback, approximate index or confidence fabrication occurs. Captions are untrusted model hypotheses; technical quality, semantic similarity and storytelling value remain distinct. Cloud adapters remain future work: explicit provider/task/data-class authorization, uploads denied by default, minimized frames and no secret serialization.

Temporal/spatial/semantic grouping uses coarse events above scenes/activities with adjustable merge thresholds. Feedback is separate user-owned state; reanalysis cannot overwrite exclusions, must-includes, manual segments or assignments. Ranking strategies alter weights, not pipelines. Storyboards reference source IDs and source times, include rationale, and remain editor-independent and editable.

## MCP and jobs (M5)

The optional official Python MCP SDK 2.x (`mcp>=2.3,<3`, MCPServer) runs stdio only. Fourteen typed tools call the same Analyzer/JobManager services as CLI. Startup-approved media roots constrain paths and catalog queries; tool calls cannot alter roots or runtime configuration. Core result schemas ship inside the wheel and MCP exposes separate job/transport schemas, bounded pages and explicit omission markers. No HTTP transport or experimental SDK Tasks API is used.

Jobs persist in a catalog-adjacent `.jobs.sqlite3` store (schema1), separate from catalog schema2. Detached processes survive transport exit; one job runs per catalog while queued workers wait (not FIFO). A 2-second heartbeat maintains a 30-second lease; expiry yields interrupted state on the next interaction and requires explicit resume. Cancellation is persisted and cooperative between files/stages/commits, not an immediate stop of model/native work. Resume increments attempt and reuses independently committed stage caches. Token/attempt/lease checks and a short jobs transaction held through catalog observe/save/fail commit fence stale worker writes. Partial media failures retain the summary and produce failed job status. Ambient provider credentials/telemetry exporters are not passed to detached workers.

## Security and operational limits

Default metadata/technical processing makes no inference/network requests. Opt-in semantics uses a local CPU encoder and explicitly allowed loopback-only Ollama HTTP; cloud-disabled status is required before model inspection/generation. Trusted local runtimes/models are not an OS sandbox: use external network policy if required. FFmpeg/ffprobe run without a shell with file/pipe protocols only. Symlink entries are skipped; selected roots resolve explicitly. Decoder bugs and path replacement remain risks. Use read-only source mounts and a sandbox for untrusted collections. Paths, metadata, embeddings and captions can be private; SQLite is not encrypted. No telemetry or cloud credentials are introduced.

Sequential processing bounds memory and GPU contention initially. Later add bounded queues, one model residency policy per device, batched inference and cancellation between bounded units. No universal GPU support promises: test model/runtime/platform combinations. Model licensing, accuracy, multilingual quality, diarization access restrictions, HEIC codecs/HDR, NAS read cost, VFR sync and event over-fragmentation are principal risks. Measure against consented, diverse fixtures before claiming quality.

## Extension points and acceptance

Add stages/providers/format extractors/exporters as ordinary modules with explicit inputs/results and stage versions. A new model must declare its revision and embedding dimension; never mix embedding spaces. A future migration is transactional and refuses newer schemas rather than silently resetting data. Research notes and roadmap record unresolved integrations. M1 acceptance: real image/audio/video metadata, stable IDs after moves, independent metadata cache reuse/invalidation, no source byte changes, isolated corrupt-file errors, JSON CLI and restart by rerun.

## Research basis

See [media/AI ecosystem](docs/research/media-ecosystem.md), [existing applications](docs/research/existing-applications.md), and [FilmCraft/MCP interfaces](docs/research/filmcraft-mcp.md) for primary-source citations and evidence limits. ExifTool is recommended for broader camera metadata but deferred rather than becoming another required executable in the foundation.
