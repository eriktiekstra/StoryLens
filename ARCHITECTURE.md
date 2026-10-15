# StoryLens architecture

Status: M1 foundation and M2 technical analysis implemented; semantic/provider/agent capabilities below remain planned.

## Scope and decisions

StoryLens is an editor-independent, local-first media intelligence library with thin CLI and, later, MCP adapters. It is not an editor or an autonomous agent. Working milestones M1–M2 cover ingestion, metadata, technical measurements, duplicate candidates and timestamped scenes; the remaining product requirements stay in the roadmap.

Use Python 3.11+, SQLite from the standard library, Pillow/NumPy for image metadata and bounded technical measurements, and external ffprobe/FFmpeg for audio/video metadata and technical processing. Python has mature bindings to native media and ML libraries. Rust offers strong resource control but would require a second ML runtime or service. TypeScript is attractive for MCP but a weaker fit for local ML. A hybrid adds packaging, IPC and versioning costs before a measured bottleneck exists. Profile before adding another language.

No server, queue broker, vector database, Docker requirement, model download or cloud account is needed for M1/M2. MIT covers original code; distributed dependencies and model weights retain their licenses. Do not bundle an arbitrary FFmpeg build: GPL/nonfree options change redistribution obligations.

## Boundaries

- **Configuration**: TOML → environment → explicit CLI options. Only implemented settings are accepted; misspellings fail.
- **Ingestion**: recursive traversal, exclusions, regular files only, streamed fingerprints, source stability checks. Content identity is independent of paths.
- **Metadata**: read-only Pillow and bounded ffprobe subprocesses. Preserve raw EXIF/ICC/XMP and ffprobe JSON alongside normalized fields. Do not infer a timezone from an EXIF timestamp lacking an offset. Filesystem mtime is not capture time.
- **Storage**: local SQLite transactions, schema version gate, indexed content identities and paths, stage-keyed successful results. Failures remain explicit and retryable.
- **Service**: orchestrates ingestion and metadata without depending on argparse, MCP, editors, or a cloud provider.
- **Technical stages**: image_technical (bounded, orientation-aware proxies and similarity features) and video_technical (source-PTS cuts, derived JPEGs, sampled frame proxies and first-stream audio statistics), independently cached from metadata.
- **CLI**: synchronous commands with JSON output and explicit partial-failure exit status. M1 is not a claim of asynchronous MCP support.

Future modules depend inward on the service and storage contracts. Add concrete modules when needed, not a dynamic plugin marketplace.

## Identity and cache invariants

Media ID is sha256:<full-file-digest>. Identical bytes share analysis; each observed absolute path has its own filesystem metadata. A move adds a path to the same identity, and missing old paths remain historical references, not deleted media. Path availability must be checked at use time. A changed path points at the new content identity; previous content results remain retained.

Every stage key covers content identity, stage name, algorithm version, tool/model revision, relevant parameters, depth and segment scope when applicable. M1 metadata includes Pillow or ffprobe version. Changing another stage must not invalidate metadata. Force recomputes the selected stage. Failed extraction never becomes a successful cache hit. Persist each successful file before proceeding; interruption resumes by rerunning the same command.

Default scans hash every supported file in bounded chunks. This is I/O-expensive on a NAS but detects same-size, same-mtime replacements. Optional stat trust reuses an identity only on matching size and nanosecond mtime; it is explicitly weaker, never the default. Detect changes during hashing/extraction using stat snapshots; concurrent adversarial writes cannot be ruled out without a filesystem snapshot. Use quiescent libraries or NAS snapshots for consistency.

SQLite stays on local storage; sources may be read-only SMB/NFS mounts. WAL requires shared-memory coordination and is inappropriate across machines on a network filesystem: https://sqlite.org/wal.html . Use short per-file transactions and a bounded busy timeout. Disk-full/storage errors abort the operation rather than pretending analysis succeeded. No source files or sidecars are written.

## Timestamp and media semantics

Store filesystem nanosecond timestamps separately from embedded timestamps. Preserve unparsed creation_time and EXIF DateTimeOriginal/offset tags; M1 does not guess capture chronology. Preserve rational frame rates, time_base, start_time, rotation side data, audio tracks, color primaries/transfer and mastering metadata from ffprobe. Average frame rate is not a timeline: future scenes use presentation timestamps in source time base, not frame_number / fps. Normalize rotation and tone-map HDR only in derived analysis artifacts, never originals; report those transforms in provenance. HEIF optional decoder support is distinct from HDR fidelity. Current M2 JPEG previews are simple 8-bit conversions with no tone mapping; this limit is explicit in result provenance. RAW metadata requires optional ExifTool; M1 reports unsupported image decoding explicitly instead of claiming full RAW support.

## Technical implementation

Stage selection is all/metadata/technical. Stage failures are isolated, and cache identity includes dependencies and relevant parameters. Missing representative JPEGs cause only video technical regeneration. Derived JPEGs live under the catalog-adjacent .artifacts tree; current-run artifacts are excluded from ingestion and failed extraction cleans only its own partial files. Retained old/forced artifacts have no automatic garbage collection. Exact duplicate queries use content identities and existing path observations; near candidates use a bounded pHash BK-tree, color/aspect checks and explicit recall limits. Nothing deletes sources or ranks emotional significance. See USAGE.md for bounds, timing semantics, signal measurements and lack of HDR tone mapping.

## Future analysis and provider interfaces

A stage receives a content reference, segment scope, parameters, cancellation token and selected provider; returns versioned data, evidence timestamps, uncertainty and provenance. Add typed task-specific interfaces (vision description, image/text embeddings, transcription), not one universal prompt function. A provider must declare capabilities; CLIP similarity is not a factual caption, Whisper is not diarization, and camera motion is not emotional value.

Provider configuration will select task/model/endpoint, credential reference, inference parameters, concurrency, timeout, retry and budget. Local endpoints are not automatically safe: network-disabled inference must disallow them too unless a separately defined local policy permits loopback. Cloud requires explicit policy authorization for provider, tasks and data classes; deny by default, minimize frames, strip GPS unless authorized, never serialize secrets. Do not add inert cloud settings before an enforcing provider exists.

Pipeline: metadata → technical measurements → scene/representative frames → local embeddings/classification → selected vision analysis → cross-media events → highlights/storyboards. Stages independently executable/cacheable. FAST/STANDARD/DEEP become exposed only once their stages exist. Technical quality, semantic relevance, storytelling estimates and confidence remain distinct. Outputs distinguish observations from model hypotheses; selection never implies factual interpretation.

Temporal/spatial/semantic grouping uses coarse events above scenes/activities with adjustable merge thresholds. Feedback is separate user-owned state; reanalysis cannot overwrite exclusions, must-includes, manual segments or assignments. Ranking strategies alter weights, not pipelines. Storyboards reference source IDs and source times, include rationale, and remain editor-independent and editable.

## MCP and jobs (M5)

Use maintained official Python MCP SDK, stdio first. Tools call the same service as CLI. Start jobs quickly, return durable IDs; status/result pages and explicit cancellation never depend solely on transport lifetime. Persist checkpoints, stage failures, progress and cancellation state. Resume interrupted jobs deliberately. Use SDK progress and cancellation as transport signals, not durability. Version JSON result schemas; use bounded pages rather than full-library payloads. Add HTTP only when justified, with authentication, origin validation and path authorization. The current researched Python SDK is v2 (MCPServer); its removed experimental Tasks API must not be used as the job engine.

## Security and operational limits

M1/M2 perform no inference or network requests. FFmpeg/ffprobe are invoked without a shell and with file/pipe protocols only. Symlink entries are skipped; the user-selected root is resolved explicitly. This is not an OS sandbox: trusted local files are the intended input, decoder vulnerabilities and path replacement races remain. Untrusted collections should run under a sandbox with read-only source mount and disabled network. Extracted metadata and errors may contain private paths/text. Store/export only to trusted locations; SQLite is not encrypted. No telemetry or credential handling in M1.

Sequential processing bounds memory and GPU contention initially. Later add bounded queues, one model residency policy per device, batched inference and cancellation between bounded units. No universal GPU support promises: test model/runtime/platform combinations. Model licensing, accuracy, multilingual quality, diarization access restrictions, HEIC codecs/HDR, NAS read cost, VFR sync and event over-fragmentation are principal risks. Measure against consented, diverse fixtures before claiming quality.

## Extension points and acceptance

Add stages/providers/format extractors/exporters as ordinary modules with explicit inputs/results and stage versions. A new model must declare its revision and embedding dimension; never mix embedding spaces. A future migration is transactional and refuses newer schemas rather than silently resetting data. Research notes and roadmap record unresolved integrations. M1 acceptance: real image/audio/video metadata, stable IDs after moves, independent metadata cache reuse/invalidation, no source byte changes, isolated corrupt-file errors, JSON CLI and restart by rerun.

## Research basis

See [media/AI ecosystem](docs/research/media-ecosystem.md), [existing applications](docs/research/existing-applications.md), and [FilmCraft/MCP interfaces](docs/research/filmcraft-mcp.md) for primary-source citations and evidence limits. ExifTool is recommended for broader camera metadata but deferred rather than becoming another required executable in the foundation.
