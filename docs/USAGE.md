# CLI and operational guide

## Guided project wizard

Run `storylens` in an interactive terminal, or the explicit equivalent `storylens wizard`. No separate scan/analyze/storyboard commands are needed for this path:

~~~sh
storylens
storylens wizard --source "/mounted/holiday" --project "$HOME/storylens-projects/holiday"
# Reopen the same project, retaining caches, feedback and saved edits:
storylens wizard --project "$HOME/storylens-projects/holiday"
~~~

The wizard chooses a mixed-media source folder and an independent local project directory, checks recognized sources and required tools, confirms analysis, shows actual checkpoint counts, asks for target film duration/strategy, and guides storyboard review. Keep the project directory local and outside the source folder, especially when media is on a NAS. A new project never adopts the global catalog or overwrites an unrelated existing directory.

Technical-only is the default **even if configured model paths exist**. Explicit local-AI mode uses installed/configured local models and selected fast/standard/deep coverage; there are no automatic downloads, cloud fallbacks or fabricated captions. Optional transcription follows current core support for standalone audio, not automatic speech extraction from videos. Tool/model configuration can be supplied with ordinary global options or `--config`; the guided mode discloses the selected runtime before analysis.

Review can browse all indexed media, require/exclude/neutralize whole-media selection, regenerate with a different budget/strategy, reorder entries, change a title or image hold/source interval, and reload deliberately edited JSON. Existing feedback, source bounds, duration budgets and whole transcript-timed conversation constraints still apply; the wizard does not silently relax them. Partial analysis failures are shown and require a deliberate decision before using remaining evidence.

Local JPEG contact sheets provide visual source review without a server. Images are orientation-aware derivatives; video tiles are stored scene samples labeled with their own source timestamps, not frame-accurate cut previews or a finished film. Audio and unavailable thumbnails are labeled explicitly. Pages are bounded to24 entries and only opened in an external viewer/browser on request. Outputs strip inherited source metadata, but still contain private visual content/filenames; protect the project directory.

Approving a storyboard is separate from editor mutation. The optional FilmCraft route checks a trusted executable and the actual supported tools/version, creates only a new project, renders a PNG preview and requires a separate export approval. The wizard creates a 1920x1080 canvas and aspect-fits visual sources before saving. Storyboard edits invalidate old approval/editor linkage; an editor file changed after preview needs a new preview. Existing exact frame-grid and nonzero-origin restrictions remain explicit errors, not hidden snapping/rebasing.

Project state, the catalog, versioned storyboard files and operation reports remain local. Interrupt with Ctrl-C or EOF and reopen the printed project directory; completed analysis stages are cached. Reopening an existing storyboard offers review or deliberate cached reanalysis/regeneration rather than silently discarding edits. Do not run multiple writers against the same wizard project at once. Only open trusted local project/config files, since runtime settings select trusted executables.

Bare invocation in a pipeline/non-terminal prints help and exits0 rather than waiting for input. Explicit `wizard` requires interactive stdin/stdout and fails clearly without them. `--help`, `--version`, existing subcommands and JSON APIs retain their noninteractive behavior. `--json` and an explicit global `--database` are rejected for wizard mode: use its `--project` or individual automation commands instead. `wizard --source`/`--project` are optional prompt conveniences, not noninteractive bypasses for consent.

The first stage is a terminal interface. A browser GUI remains a later stage; no HTTP server, port or automatic browser session is introduced. Reusable project orchestration lives outside terminal input/output so that a future frontend can share the same analysis, state, authorization and approval checks.

## CLI reference

Global options precede the command: storylens [OPTIONS] COMMAND ... .

| Command | Behavior |
| --- | --- |
| wizard [--source DIR] [--project DIR] | Guided terminal project workflow; also the default for bare interactive storylens |
| scan PATH | Recursively index recognized files and full SHA-256 identities; no media decode |
| analyze PATH [--stage all/metadata/technical/semantic/transcript] [--depth fast/standard/deep] [--force] | Independently cached stages; defaults to metadata+technical; semantics/transcription require opt-in model configuration |
| list [--kind image/video/audio] [--limit N] [--after CURSOR] | Bounded identity page; default 50, maximum 500 |
| show MEDIA_ID | Last successful result per stage, provenance, path observations and failures |
| export [--kind KIND] [--limit N] [--after CURSOR] | JSON page including paths and analysis; follow next_cursor until null |
| duplicates [--mode exact/near] [--distance 0..64] [--limit N] [--after CURSOR] | Exact byte-identity groups or conservative image near-duplicate pairs; exact is default |
| scenes MEDIA_ID [--limit N] [--after CURSOR] | Timestamped scenes and representative JPEG references from stored video analysis |
| search (--text QUERY / --image PATH) [--depth fast/standard/deep] [--limit N] | Top same-space cosine matches from stored embeddings; default depth standard, limit20 (max500); no pagination |
| transcript --media-id ID [--format json/srt/vtt] [--offset SECONDS] [--language CODE] PATH | Import supplied timestamped text without model inference |
| events [--limit N] [--after CURSOR] | Page coarse event candidates from stored evidence and corrections |
| event EVENT_ID [--limit N] [--after CURSOR] | Page member evidence for one automatic/manual/undated event |
| event-create TITLE | Register a stable manual event ID for explicit member assignment |
| event-rename EVENT_ID TITLE | Rename a registered manual event |
| feedback MEDIA_ID [--set JSON | --clear FIELD... | --clear-all] | Read or patch independent corrections |
| preferences [--set JSON] | Read or patch grouping thresholds |
| storyboard [--duration SECONDS] [--strategy balanced/technical/story] [--weights JSON] [--required ID] [--excluded ID] [--image-seconds N] [--clip-seconds N] [--text QUERY] [--depth fast/standard/deep] | Complete portable storyboard JSON from stored evidence; no inference unless text is supplied |
| storyboard-check PATH | Validate edited storyboard JSON against current source coverage and feedback; emit complete validated JSON |
| job --allow-root DIR start/batch/status/cancel/resume/result/list ... | Durable local work, bounded batch admission and FIFO scheduling; JSON output |
| serve --allow-root DIR | Optional official-SDK MCP stdio server; repeat roots explicitly |
| benchmark generate DIR / benchmark run PATH --workspace DIR | Generated fixtures or isolated model-free cold/warm catalog measurements and optional profiles |

Global options: --config FILE, --database FILE, --ffprobe EXECUTABLE, --ffmpeg EXECUTABLE, --timeout SECONDS, --scene-threshold NUMBER, --minimum-scene-seconds NUMBER, --max-scenes N, --embedding-model DIRECTORY, --vision-model NAME, --ollama-endpoint ORIGIN, --semantic-timeout SECONDS, --max-audio-seconds SECONDS, --audio-transcription-model DIRECTORY, --audio-transcription-device cpu/cuda, --audio-transcription-language CODE, repeatable --exclude PATTERN, --trust-stat / --no-trust-stat, --json, --version. Help is available per command.

Exclusions replace configured patterns. Each matches a POSIX-style relative path or basename, case sensitively; matching directories are pruned. Use --exclude 'private' to prune any directory with that name. No gitignore syntax, negation or regex is implied. Traversed symlink entries are skipped; explicitly selected roots are resolved to their real target. The current catalog's derived-artifact subtree is excluded from source ingestion even if the catalog is within the chosen scan root.

Exit codes: 0 success; 1 configuration/storage/fatal error; 2 partial file/traversal failure (also argparse usage error); 130 interrupted. JSON mode emits one result object to stdout for operations and handled fatal errors. Argparse usage and interrupt diagnostics go to stderr. Partial summaries retain at most 100 error details with path/stage; errors_truncated discloses overflow. Storage errors abort. Completed stages survive restart; rerun synchronous commands or deliberately resume a durable job.

Summary analyzed counts files with at least one freshly computed stage; cache_hits counts files with all selected applicable stages cached; failed counts files with an ingestion/stage failure. A file can be both analyzed and failed when one stage succeeds and another fails. Per-stage counters give the exact breakdown. Standalone audio technical analysis reports RMS/peak, clipping/silence fractions and amplitude-threshold intervals; nonsilence is not evidence of speech. A failed forced run can coexist with a prior successful result; inspect failures and analyzed_at.

## Configuration

No config is loaded implicitly. Supply --config. Precedence: defaults < TOML < environment < explicit CLI options. TOML/environment paths are relative to process cwd, not the config file location. Database/source paths support home expansion.

~~~toml
[storylens]
database = "/absolute/local/path/catalog.sqlite3"
ffprobe = "ffprobe"
ffmpeg = "ffmpeg"
timeout = 30.0
scene_threshold = 0.3
minimum_scene_seconds = 0.5
max_scenes = 256
exclusions = ["private", "@eaDir", ".snapshot"]
trust_stat = false
# Optional: only used when semantic stages/search are explicitly requested.
embedding_model = "/absolute/models/siglip"
vision_model = "moondream:latest"
ollama_endpoint = "http://127.0.0.1:11434"
semantic_timeout = 120.0
max_audio_seconds = 1800.0
# Optional: explicitly installed faster-whisper model, never downloaded automatically.
audio_transcription_model = ""
audio_transcription_device = "cpu"
audio_transcription_language = ""
~~~

Unknown keys, invalid booleans, nonpositive/nonfinite timeouts and scene spacing, threshold outside (0,1], and nonpositive/noninteger max_scenes fail. Environment variables use STORYLENS_ plus each uppercase setting name, including EMBEDDING_MODEL, VISION_MODEL, OLLAMA_ENDPOINT and SEMANTIC_TIMEOUT. Exclusions use OS path separators (colon POSIX, semicolon Windows); use TOML/CLI for patterns containing that separator. TRUST_STAT accepts true/false. No credentials are needed.

Default database: Linux $XDG_DATA_HOME/storylens/catalog.sqlite3 or ~/.local/share/storylens/catalog.sqlite3; macOS ~/Library/Application Support/storylens/catalog.sqlite3; Windows %LOCALAPPDATA%/storylens/catalog.sqlite3. Keep SQLite local; SMB/NFS/Synology media mounts are supported as source references. A selected catalog path can reuse an existing compatible catalog; no old command/config aliases are retained and no unrelated application data is moved automatically.

## Media support

Extension discovery is an allowlist, not proof of decoder support. Unknown extensions are skipped in directories and rejected as a single selected file.

| Media | Implemented behavior |
| --- | --- |
| JPEG, PNG, WebP, TIFF, BMP, GIF, AVIF | Pillow metadata and first-frame image technical analysis, subject to installed codec support |
| HEIC/HEIF | Recognized; optional pillow-heif required; unsupported decoding fails per file; no HDR fidelity guarantee |
| DNG, CR2/CR3, NEF, ARW, ORF, RW2, RAF | Identity indexing guaranteed; extraction can fail with unsupported decoder. Full RAW awaits optional ExifTool/LibRaw work |
| MP4, MOV, MKV, AVI, M4V, WebM, MTS/M2TS, 3GP, MPG/MPEG | ffprobe metadata plus FFmpeg technical analysis, codec/filter/build dependent |
| MP3, AAC, WAV, FLAC, M4A, OGG, Opus, AIFF/AIF, WMA | ffprobe metadata; bounded standalone signal/silence analysis; optional local transcription |

Sources keep all original metadata because they are never rewritten. The catalog preserves extractor-visible tags, not a complete parser for every camera maker note. Binary image metadata is base64; EXIF rational values keep numerator/denominator. Available GPS is normalized without guessing missing data. Raw ffprobe JSON preserves stream/rotation/HDR fields. Its embedded filename can be historical after moves; use catalog path observations for references. Missing tags remain absent/null. EXIF timestamps lacking offsets remain unzoned strings. Filesystem mtime/ctime are nanosecond integers; ctime usually means metadata change, not capture. Birth time is retained when exposed by the OS.

## Image technical analysis and duplicates

The first frame is decoded under a 50-million-source-pixel limit, reduced to maximum dimension 1024, EXIF-oriented, and converted to encoded RGB; alpha composites on white. ICC profiles are retained by metadata but not color-managed in technical analysis. JPEG draft decoding reduces unnecessary work.

Sharpness is a normalized Laplacian-variance proxy; blur_proxy is its complement, not a focus diagnosis. Exposure measures encoded luminance/clipped fractions at <=5/255 and >=250/255. Contrast is normalized luminance deviation; noise uses a smooth-region Laplacian MAD estimator. Scene content, texture, compression and resize affect all of these. Scores never imply emotional/storytelling relevance and never delete/discard files.

Exact duplicates share a full content ID and at least two currently existing non-symlink path observations. Missing historical paths are not called duplicate files. Near-duplicates require current-version cached image features and return source ID pairs with Hamming/color/aspect distances. Use show to resolve paths. Re-scan before acting: queries represent indexed identities, not proof that files were unchanged since indexing.

Near matching uses a 64-bit DCT pHash, RGB 4x4x4 histogram, <=3% aspect difference and <=0.18 histogram total-variation difference; nearly flat images (luminance deviation <0.02) are excluded. This is conservative duplicate candidacy, not semantic composition search. BK-tree search visits at most 4096 hash nodes and considers the first 512 later IDs per image. The result search object discloses these recall limits; true pairs may be omitted. Equal-hash groups are expanded lazily, never as a materialized quadratic cross product. Pagination assumes unchanged records and distance; the cursor is opaque and bound to distance.

## Video scenes and signal measurements

The first non-cover-art video stream is analyzed; cover-art-first ordering is explicitly unsupported. Source best-effort integer presentation timestamps and rational time bases are retained. Source seconds are absolute, not normalized to zero. For editing, subtract source_timing.origin_seconds only when the target editor expects zero-based clip time. VFR positions never use frame-number / average-fps. Duration/end evidence and boundary scores are retained; scene_score is not a calibrated confidence probability.

FFmpeg scene scores detect candidate cuts; minimum spacing merges short candidates. Each scene has a real JPEG from its midpoint or an in-scene fallback for sparse timestamps. FFmpeg autorotation applies display metadata. The JPEGs are bounded previews, **not HDR tone-mapped masters**. HDR tags/limitations are disclosed, and 8-bit luma proxies must not be interpreted as source HDR photometry.

At most 64 approximately uniform frames provide blur/exposure/black-frame and temporal frozen/motion/shakiness proxies. Sampling is explicitly non-exhaustive; it can miss defects between samples. Motion is simple bounded translation/luma difference, not optical-flow subject tracking or a reliable shake diagnosis. The first audio stream is decoded across all channels for RMS/peak, digital silence, abs<=0.001 silence fraction and near-full-scale clipping fraction. These are pooled signal statistics, not time-aligned silence intervals, LUFS, speech/music detection or an audible-clipping guarantee. No audio yields status=no_audio.

Artifacts live under the catalog's basename with .artifacts suffix, grouped by content and stage cache key, not next to sources. Missing JPEGs invalidate only that video technical result. Each extraction owns a unique artifact subdirectory; failures remove only that run's partial outputs. Retained older cache versions/forced-run artifacts are not automatically garbage-collected. Keep the artifact tree with catalog backups if you want cache hits without regeneration.

## Local semantics and retrieval

Install and stage the local models deliberately as described in [integration setup](INTEGRATIONS.md#local-ai-setup-m3). Inference is opt-in; `--depth` is valid only with all/semantic analysis, never metadata/technical/transcript. FAST performs embeddings without captions. STANDARD and DEEP run independently cached `semantic_embeddings` and `semantic_vision` stages; a vision failure does not erase embeddings or technical results.

| Depth | Image | Video scene representatives | Tasks |
| --- | --- | --- | --- |
| fast | First decoded image frame | Middle scene representative | Image embeddings |
| standard | First decoded image frame | Up to eight uniformly spaced scene indices, including first/last when multiple | Image embeddings and captions |
| deep | First decoded image frame | Every detected scene representative, bounded by max_scenes | Image embeddings and captions |

This is semantic coverage, not a guarantee that FAST avoids the full video technical pass. Video semantics requires successful current-version technical analysis and its JPEGs, preserving source PTS/time-base evidence; a failed forced technical pass cannot silently fall back to old frames. Coverage discloses selected/available counts. Captions attach scene index, scene bounds and actual representative-frame timing; still evidence identifies the image. No temporal reasoning, face identity, speech or event grouping is claimed. Derived video frames retain M2 HDR/rotation limits.

Image preprocessing corrects EXIF orientation, composites alpha on white, bounds source pixels to50 million and longest side to1024, and removes metadata before inference. CLIP/SigLIP applies its installed processor afterwards; color is not ICC-managed. Model weights/tokenizer/processor content, runtime versions, depth, task, prompt/options and video dependency key form semantic cache identity. Changing vision does not invalidate embeddings or metadata; changing embedding content/processing selects a distinct space. Mutating a loaded model fails explicitly; restart with an immutable snapshot.

`search --text ...` embeds language with the same encoder as stored images; `search --image PATH` embeds the supplied image without indexing it. Select the same --depth used for analysis. Only compatible space/depth/algorithm results are compared; no cross-model projection or caption-keyword fallback occurs. Empty compatible indexes return an empty items array. Search retains historical indexed media even if source files have since moved/disappeared; use show and re-scan before acting. Same-space dimension corruption fails, rather than producing a misleading similarity.

Ranking uses exact normalized cosine similarity, maximum selected-frame score per media; ties sort by content ID. Scores in [-1,1] are not probabilities or quality/storytelling scores. A bounded top-N result is returned, without a cursor; execution scans compatible stored vectors linearly (no approximate index or large-NAS performance claim). Evidence comes from the winning frame. Captions are untrusted `model_hypothesis` with null confidence and may hallucinate even simple details; do not execute model text as agent instructions.

Encoder loading/inference is synchronous on CPU without an enforceable per-call deadline. semantic_timeout (default 120 seconds) bounds each Ollama HTTP request; responses are capped at 4 MiB and captions/text inputs at 32768 characters. Text is tokenized with truncation at the installed model token limit. No retries occur. Synchronous Ctrl-C exits; completed stages remain cached. Durable jobs checkpoint between files/stages, not individual frames within a multi-frame semantic stage.

## Limits, hardware and NAS operation

Default analysis uses no model/GPU; optional semantics adds local CPU encoder/model memory and an external loopback vision daemon. Processing is sequential; hashes read 1 MiB chunks. Image decoding may use substantial memory up to its source-pixel limit. Video permits <=16,777,216 source pixels, 1..32 audio channels, and max_scenes=256 by default (configurable). Scene-limit exceedance fails explicitly, never returns silently truncated complete analysis. Technical subprocesses have finite timeouts (default 30 seconds); raise timeout for long clips/NAS reads. Technical probe/error/JPEG payloads are bounded and timeline/audio output streamed. Metadata-only probing retains its original uncapped capture behavior; embedded metadata can still be large.

Video timeline/cut passes decode content, not just container headers. Scene JPEGs use seeking and small derivatives. No general throughput/RAM floor is established; benchmark your codecs/NAS. Exclude @eaDir and snapshots. Default full hashing is I/O-expensive but detects same-size, same-mtime replacement; trust_stat explicitly weakens that guarantee. Use snapshots/quiescent libraries for consistency.

WAL requires local coordination; do not share the database across hosts on network storage. Use SQLite backup facilities or copy only while no StoryLens process is active, including durable workers and artifacts as desired. The job database uses the catalog basename with `.jobs.sqlite3` suffix; include it in backups. CLI page limits bound record count, not total embedded metadata bytes; MCP additionally bounds response size and discloses omissions. Source locations remain historical observations and availability must be checked before editing. There is no garbage-collection command yet.

## Durable local jobs (M5)

Global options still precede `job`. Approved roots are explicit, repeatable existing directories; put them before the job action:

~~~sh
storylens --database /local/catalog.sqlite3 job --allow-root /mounted/media start /mounted/media --stage technical
storylens --database /local/catalog.sqlite3 job --allow-root /mounted/media status JOB_ID
storylens --database /local/catalog.sqlite3 job --allow-root /mounted/media result JOB_ID
storylens --database /local/catalog.sqlite3 job --allow-root /mounted/media cancel JOB_ID
storylens --database /local/catalog.sqlite3 job --allow-root /mounted/media resume JOB_ID
storylens --database /local/catalog.sqlite3 job --allow-root /mounted/media list --limit 20
~~~

`start --scan` indexes without analysis; other start flags mirror analysis stage/depth/force selection. Every job command returns JSON. Submission returns a durable ID without waiting for media decode. Workers survive command/server exit and persist request, progress, summary, errors and cancellation. Job control never expands the roots supplied at launch. On Windows, durable launch requires the local Windows PowerShell/.NET WMI process provider: it creates a worker outside the MCP host kill-on-close Job Object. Requests are JSON data in a private launcher environment value, not interpolated shell commands; the worker receives only the original allowlisted environment. A denied/unavailable provider is an explicit launch failure. Startup has a 20-second timeout. POSIX uses an independent process session.

States distinguish queued/running/cancel_requested/succeeded/failed/cancelled/interrupted. Cancellation is cooperative at entry/stage boundaries, not an immediate process kill; a current hash or model/decoder stage may finish before cancellation is acknowledged. Committed stages remain reusable. Explicit resume uses current cache keys and does not repeat already cached successful stages merely because the original submission used force. Progress is checkpoint-based, with totals unknown until traversal finishes. Failed media retain bounded per-stage error details; inspect results rather than assuming every indexed item succeeded.

M9 `job --allow-root DIR batch PATH1 PATH2 ...` accepts 1..32 distinct canonical roots with the same `--scan`, stage/depth/force options as `start`. Every option/root is validated before any submission side effects; all records commit before launches. Results preserve input order, including durable failed children if individual worker startup fails. Read each ID's status; launching a batch is not a transaction over media analysis. One job runs per catalog in enqueue-time/ID FIFO order. Resume keeps original creation time and joins the tail. There is one waiting process per queued job, so submit bounded workloads deliberately rather than treating this as a host-wide admission quota. Windows batch startup may wait for sequential WMI launches (at most 20 seconds per launch); pending records reserve that launch time before their ordinary worker lease.

## Events and feedback (M6)

`events`, `event ID`, `event-create TITLE`, `event-rename ID TITLE`, `feedback MEDIA_ID [--set JSON|--clear FIELD...|--clear-all]` and `preferences [--set JSON]` all emit JSON. See the [API contract](API.md#events-and-durable-corrections-m6) for full schema details.

Event grouping is conservative: no model loading, no inference, no filesystem mtime guessing and no inferred timezone. Automatic events get metadata-date titles with source/cache-key provenance. Manual events provide stable IDs and user labels. Set `event_id` in `--set` to merge/split; use `--clear event_id` (or `--clear-all`) to return a media to automatic grouping. Null `capture_time`/`gps` means unknown, not automatic evidence.

`--clear` accepts one or more of `selection`, `capture_time`, `gps`, `event_id`, `segments`; `--clear-all` removes every override. Images cannot carry `segments`; audio/video segments require absolute source coverage and cannot include overlapping opposite selections.

## MCP stdio (M5)

Install `python -m pip install -e ".[mcp]"`, then launch `storylens --database /local/catalog.sqlite3 serve --allow-root /mounted/media`. No listener is opened. Use the trusted installed executable in your host configuration, never an automatic package/model download command. Stdout is reserved for MCP protocol traffic; diagnostics go to stderr. See [Codex/Claude setup](INTEGRATIONS.md#mcp-setup-for-codex-and-claude-code) and [bounded result contracts](API.md).

The server now also exposes event/feedback tools (M6): `list_events`, `show_event`, `create_event`, `rename_event`, `get_feedback`, `set_feedback`, `reset_feedback`, `get_preferences` and `set_preferences`. Use these to review candidates, assign manual events and adjust grouping thresholds without provider/path configuration changes.

## Recommendations and editable storyboards (M7)

~~~sh
storylens storyboard --duration 60 --strategy balanced > storyboard.json
storylens storyboard --duration 90 --required 'sha256:ID' --excluded 'sha256:OTHER_ID'
storylens storyboard --weights '{"technical":0.2,"relevance":0.3,"story":0.5}'
# Explicitly opt into the configured local encoder, over already stored embeddings:
storylens --embedding-model /models/siglip storyboard --text "holiday at the beach" --depth fast
storylens storyboard-check storyboard.json
~~~

Repeat `--required`/`--excluded` for multiple identities. They merge with independent feedback: whole-media `include` requires at least one selection, `exclude` forbids selection, include segments mandate their full union, and exclude segments prohibit overlap. Conflicts, unknown requested IDs, missing required sources, unknown required timing and impossible mandatory budgets are errors, never silently weakened constraints. Optional media without valid timing are skipped with warnings. A whole-media include does not require the entire source.

Separate scores lie in [0,1]; missing technical/relevance evidence is null. Image technical scores average available sharpness and unclipped-exposure proxies. Video uses sampled frame sharpness/luma proxies within the candidate plus available source-wide audio clipping; audio uses one minus clipping fraction. Relevance maps same-space cosine as `(cosine+1)/2`; absent query evidence is not a negative judgment. `--text` performs explicit local query inference through the existing search contract; only the top 500 compatible matches receive relevance, and larger collections disclose this limit.

Story is a disclosed heuristic: half inverse eligible event membership plus half transcript-timed conversation coverage. Undated review buckets do not count as factual events. It is not emotional importance, semantic comprehension, confidence, or a guarantee of diverse event representation.

| Strategy | Technical weight | Relevance weight | Story weight |
| --- | --- | --- | --- |
| balanced | 0.40 | 0.25 | 0.35 |
| technical | 0.80 | 0.10 | 0.10 |
| story | 0.15 | 0.15 | 0.70 |

Custom weights require exactly these three nonnegative finite components, at least one positive. Weights normalize to sum 1; each candidate's total renormalizes over available components. No available positive-weight evidence yields total 0.

Imported transcript timing takes precedence over inferred transcription. Overlapping cues and gaps <=2 seconds form atomic conversation blocks: selected blocks remain whole, even across scene boundaries. A mandatory interval expands to cover intersecting blocks. An excluded span interrupting a block makes that entire block unavailable; a conflicting mandate fails. Oversized optional conversations are skipped with warnings, not trimmed. This preserves timing continuity, not verified meaning; missing transcripts cannot establish conversation boundaries.

Default image holds are 4 seconds; nonspeech clips target 10 seconds, split at stored scene boundaries and exclusions. Required intervals allocate first. Whole-media requirements reserve feasible candidates for all required identities before score preference; remaining candidates pack greedily by score, media ID and source start. Nothing is shortened to fill the remaining budget. This is deterministic, not optimal knapsack packing. Output defaults to media-ID/source-time order, not capture chronology or a finished dramatic sequence.

Portable [storyboard-v1 JSON](../src/storylens/schemas/storyboard-v1.schema.json) contains source IDs, absolute half-open source times (null for images), durations, separate scores, required/excluded sets, rationale and caveats; no paths or transcript text. Edit title, playback order, image holds and source intervals in JSON, update each duration and `total_seconds`, then run `storyboard-check`. Current feedback, source coverage, speech-block integrity, duplicates/overlaps and the hard duration budget are revalidated. Do not erase required/excluded arrays to bypass feedback. Rescanned moved bytes keep their IDs. Rescan changed media before relying on indexed evidence; validation does not rehash sources.

Bounds: at most 10,000 current referenced media, 100,000 generated candidates, 500 selected entries, and 2 MiB CLI input JSON. Overflow is an explicit error, not a truncated result. Use longer clip targets or a smaller catalog when candidate generation exceeds its bound. MCP returns complete storyboards up to its 131,072-byte serialized response budget; larger documents fail explicitly and need a shorter duration or local CLI export. These commands do not write sources or render; optional editor exchange is separate.

## Optional FilmCraft interchange (M8)

Install `storylens[mcp]` and obtain a trusted FilmCraft **0.9.0** CLI separately. StoryLens never downloads it automatically. Use its absolute executable path; no shell or desktop bridge is launched. This workflow keeps the storyboard portable and creates a new `.fcproj`, never overwriting an existing editor project.

~~~sh
storylens filmcraft --executable /opt/filmcraft/bin/filmcraft-cli discover
storylens storyboard --duration 60 > storyboard.json
# Review/edit the storyboard, its source ranges, exclusions and required media:
storylens storyboard-check storyboard.json
storylens filmcraft --executable /opt/filmcraft/bin/filmcraft-cli create storyboard.json \
  --output /local/edits/holiday.fcproj --allow-root /mounted/media --approve \
  --fps 24 --width 1920 --height 1080
storylens filmcraft --executable /opt/filmcraft/bin/filmcraft-cli preview \
  --project /local/edits/holiday.fcproj --output /local/edits/review.png \
  --seconds 1 --allow-root /mounted/media --approve
# Inspect the PNG and project before separately approving export:
storylens filmcraft --executable /opt/filmcraft/bin/filmcraft-cli export \
  --project /local/edits/holiday.fcproj --output /local/edits/holiday.mp4 \
  --allow-root /mounted/media --approve
~~~

Keep the catalog selection consistent with global `--database` before `filmcraft`. Output parents must exist; outputs must be new paths outside every approved source root. Repeat `--allow-root` for additional source directories. Missing approval, changed source hashes, unsupported versions, private/proxy/offline source references and existing outputs fail. Creation checks current storyboard feedback again and rehashes every selected source before import and before save. Editing source files requires rescanning first. The external editor is trusted, not sandboxed; use OS confinement/read-only mounts for stronger privacy.

Supported sequence rates: 24, 25, 30, 50, 60, 24000/1001, 30000/1001 and 60000/1001. Entry durations must represent whole sequence frames (only <=1 ns decimal representation error is tolerated); off-grid/sub-frame entries fail rather than silently moving cuts across exclusions or speech boundaries. Edit holds/durations or choose another rate, then revalidate the storyboard. Source in points remain absolute presentation times and convert to 254,016,000,000 ticks/second. Zero origins are supported; positive MP4/MOV/M4V origins require the editor's imported duration to equal catalog presentation end. Negative, rebased or unknown nonzero mappings fail rather than guessing an offset. Speed/reverse/edited-sequence remapping are not added.

Preview is a new PNG at a time inside the active sequence; export is an entire-sequence H.264/AAC MP4 with software encoding. Container/audio padding can exceed video duration slightly: use inspected sequence ticks and video frame count for cut/budget timing. All serialized file-backed project sources must be approved and online, including unused assets. Proxy/offline media and unsupported native project schemas are rejected before rendering. Writes publish only completed artifacts and never overwrite existing files. Reports contain per-operation results/errors; partial in-memory edits are not transactional and are discarded on session close. Inspect any failure, never blindly retry a non-idempotent command.

Import a **saved source transcript** without another transcription run:

~~~sh
storylens filmcraft --executable /opt/filmcraft/bin/filmcraft-cli transcript \
  --project /local/edits/holiday.fcproj --item-id 4 --media-id 'sha256:SOURCE_ID' \
  --allow-root /mounted/media --approve
~~~

Use an actual item ID from editor inspection, never a filename-derived ID. Only schema 15 saved local-file media transcripts are supported. The item's authorized file must match the catalog SHA-256; supplied word ticks/speaker labels are preserved in ordinary transcript results, checked against source coverage, and remain unverified speech evidence. Derived sequence transcript times are not source times and are not imported. Word runs with the same speaker and gaps <=2 seconds become segments without discarding original words. Storyboard, project and transport JSON are bounded to 2 MiB each; discovery/result failures are explicit, never truncated edit plans.

`--request-timeout SECONDS` belongs after `filmcraft` and before its action (default 300 per request); a timeout closes the SDK session and does not automatically retry. Export jobs are editor-owned, not StoryLens durable analysis jobs. The generated-fixture Linux runtime was verified; user review of private holiday media and desktop/cross-platform workflows remain acceptance work.

## Release operation and profiling (M9)

### Isolated collection benchmarks

~~~sh
# Generated test patterns, random RGB images and tones; no personal files/models:
storylens benchmark generate /tmp/storylens-fixture --images 1000 --videos 2 --audio 2
storylens benchmark run /tmp/storylens-fixture \
  --workspace /local/benchmarks/generated-001 --passes 2 --profile
# Later, measure an explicitly selected real NAS collection without changing it:
storylens benchmark run /mounted/media \
  --workspace /local/benchmarks/nas-001 --stage all --passes 2 --profile
~~~

Generation requires a new directory with an existing parent. Counts are bounded to 100,000 images and 1,000 each audio/video, with at least one source; square images are 16..4096 pixels and generated clips 1..60 seconds. `--seed` controls pixels, not codec/library implementation: exact source bytes require the same recorded environment. The manifest includes file-relative paths, byte counts and SHA-256 identities. Synthetic data does not evaluate emotional relevance, recognition accuracy or camera/codec fidelity.

Run workspaces must be new and outside the source collection. The command never opens/reuses the configured user catalog: it creates its own catalog and artifacts. `metadata`, `technical` or `all` stages are supported, with 2..10 passes; all configured model inference is disabled. Default full hashing still runs on warm passes. Explicit global `--trust-stat` changes that guarantee and is recorded; do not silently compare it with strict hashing.

`benchmark.json` records actual counters, wall/process-CPU time, parent-process high-water RSS (null if unavailable), throughput, environment and caveats. `--profile` additionally retains pstats files and top cumulative-time functions. Profiles affect timing; RSS is cumulative across passes and excludes subprocesses. Inspect profiles with `python -m pstats /local/benchmarks/generated-001/pass-01.pstats`. **Cold/warm mean catalog state only**: the inventory traverses/stats first, OS/NAS caches are not flushed, and no physical-read bandwidth or distributed/NAS guarantee is inferred. Reports and profiles can reveal paths; protect them like catalogs. Partial media failures remain in each summary, produce a failed report/nonzero exit and are not converted into benchmark success.

### Conservative GPU admission

CUDA audio-model extraction takes one advisory OS file lock before launching the actual inference worker. CPU extraction does not take it. Lock wait and inference each have their own configured audio timeout; a failed wait is explicit, never a CPU fallback. No GPU probe, model download, tensor batching, automatic device selection or claim of GPU capacity is added.

All cooperating catalogs/models/visible-device settings use one `storylens/resources/cuda.lock` under the same user's local cache: Linux `XDG_CACHE_HOME` or `~/.cache`, macOS `~/Library/Caches`, Windows `%LOCALAPPDATA%` or `~/AppData/Local`. Keep that cache local and shared between processes/containers intended to coordinate. Other users/cache roots, hosts and unrelated GPU programs are outside its scope. POSIX directories/file are private; Windows inherits the user-cache ACL. The empty file persists; **do not delete it to break a lock**, since replacing its inode splits ownership. Closing or crashing the owner releases the OS lock automatically. Real GPU inference/performance acceptance remains separate.

### Optional Docker operation

The Dockerfile is a self-hosting recipe, not a published image. Its base digest and direct core versions are pinned; apt/transitive resolution is not a byte-reproducible container lock. It includes Debian FFmpeg, no models, credentials or private media. Runtime defaults to unprivileged UID/GID10001, no listener, and CLI help. Build `--build-arg MCP=1` explicitly for the optional stdio SDK; default `MCP=0` remains core-only.

~~~sh
docker build -t storylens:0.9.0 .
mkdir -p /local/storylens-state
docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --pids-limit 256 \
  --tmpfs /tmp:rw,noexec,nosuid,size=512m \
  --user \"$(id -u):$(id -g)\" \
  --mount type=bind,src=/mounted/media,dst=/media,readonly \
  --mount type=bind,src=/local/storylens-state,dst=/state \
  storylens:0.9.0 --database /state/catalog.sqlite3 analyze /media
~~~

Use real absolute host paths and a state directory writable by the selected UID/GID; Windows/desktop path and user syntax differ. Source writes are denied by the read-only bind mount, not just application policy. Keep all catalog/feedback/jobs/artifact state on the writable local state mount; never place SQLite WAL on a network filesystem. A read-only root plus temporary `/tmp` still permits decoder scratch data, bounded by the chosen tmpfs/process/resource policy. Docker privileges/host mounts/egress remain operator responsibilities.

The example is synchronous. **Container exit stops its detached workers too**: a one-shot `job start` container does not provide host-durable execution. Keep the worker's container alive for durable job workflows, and inspect leases/resume after interruption. Do not claim that a subprocess survives container deletion. Model/GPU-enabled images and a desktop FilmCraft runtime are not bundled or certified here. Before redistributing a built image, review Debian FFmpeg/native notices and corresponding-source obligations in the [release/license guide](RELEASE.md#container-recipe-is-not-a-binary-redistribution-clearance); StoryLens MIT does not relicense those binaries.

### Operational failure matrix

| Condition | Expected behavior / recovery | Evidence boundary |
| --- | --- | --- |
| Corrupt/unsupported media | File/stage error; continue unrelated files, retain explicit failure and successful caches | Generated-media regression/smoke |
| Missing ffprobe/FFmpeg or stage timeout | Explicit stage error; no fabricated analysis; install trusted tools or adjust timeout deliberately | Automated subprocess tests and CLI smoke |
| SQLite capacity exhausted | Storage error escapes; failed write is not cached, earlier committed evidence survives | Real SQLite `max_page_count` exhaustion; whole-filesystem/inode exhaustion remains acceptance work |
| Worker/host/container interruption | Lease expires to interrupted; explicit resume reuses committed caches | Actual child-process/lease tests; physical NAS outage/host power loss remains acceptance work |
| Queued/running cancellation | Distinct requested/terminal states; cooperative boundaries, then process teardown | Automated durable lifecycle tests; terminal state is not a process-exit signal for backup/cleanup |
| Invalid/private batch root | Reject the whole request before job insertion/launch | CLI/MCP authorization/preflight tests |
| GPU lock contention/crashed owner | Wait boundedly or fail; OS releases dead owner's lock, never CPU fallback | Independent-process OS-lock tests; CUDA capacity/OOM/driver recovery remains real-hardware work |
| Changed, missing or unapproved editor source | Reject identity/path/timing or offline placeholders; never silently retarget | Generated-media/root/timing checks; private holiday review deferred |
| Read-only source deployment | Source bytes remain read-only; all state/derivatives use separate local mounts | Local Linux container smoke; other container hosts remain acceptance work |
| Incomplete dependency/license closure | Inventory emits issues and nonzero exit; unknown legal evidence is not inferred | Installed-metadata inventory plus cited upstream license review; final binary/native redistribution needs separate approval |

Back up only quiescent state, after workers have actually exited, or use SQLite backup APIs for live individual databases. Include the catalog, `.feedback.sqlite3`, `.jobs.sqlite3` and artifact tree; independent database backups are not a coordinated multi-database snapshot. Retain original read-only media independently. The project does not encrypt backups, collect telemetry, manage model/native licenses or provide a distributed NAS scheduler.

Reproducible wheel/sdist checks, isolated core/MCP installation and a marker/extra-aware dependency/license inventory are documented in [RELEASE.md](RELEASE.md). Private-media review, real NAS/GPU/codec performance and final human release acceptance remain deferred until implementation is complete, as requested.

