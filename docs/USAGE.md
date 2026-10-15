# CLI and operational guide

## CLI reference

Global options precede the command: storylens [OPTIONS] COMMAND ... .

| Command | Behavior |
| --- | --- |
| scan PATH | Recursively index recognized files and full SHA-256 identities; no media decode |
| analyze PATH [--stage all/metadata/technical/semantic] [--depth fast/standard/deep] [--force] | Independently cached stages; all defaults to metadata+technical, depth explicitly opts into semantics; semantic-only defaults to standard |
| list [--kind image/video/audio] [--limit N] [--after CURSOR] | Bounded identity page; default 50, maximum 500 |
| show MEDIA_ID | Last successful result per stage, provenance, path observations and failures |
| export [--kind KIND] [--limit N] [--after CURSOR] | JSON page including paths and analysis; follow next_cursor until null |
| duplicates [--mode exact/near] [--distance 0..64] [--limit N] [--after CURSOR] | Exact byte-identity groups or conservative image near-duplicate pairs; exact is default |
| scenes MEDIA_ID [--limit N] [--after CURSOR] | Timestamped scenes and representative JPEG references from stored video analysis |
| search (--text QUERY / --image PATH) [--depth fast/standard/deep] [--limit N] | Top same-space cosine matches from stored embeddings; default depth standard, limit20 (max500); no pagination |

Global options: --config FILE, --database FILE, --ffprobe EXECUTABLE, --ffmpeg EXECUTABLE, --timeout SECONDS, --scene-threshold NUMBER, --minimum-scene-seconds NUMBER, --max-scenes N, --embedding-model DIRECTORY, --vision-model NAME, --ollama-endpoint ORIGIN, --semantic-timeout SECONDS, repeatable --exclude PATTERN, --trust-stat / --no-trust-stat, --json, --version. Help is available per command.

Exclusions replace configured patterns. Each matches a POSIX-style relative path or basename, case sensitively; matching directories are pruned. Use --exclude 'private' to prune any directory with that name. No gitignore syntax, negation or regex is implied. Traversed symlink entries are skipped; explicitly selected roots are resolved to their real target. The current catalog's derived-artifact subtree is excluded from source ingestion even if the catalog is within the chosen scan root.

Exit codes: 0 success; 1 configuration/storage/fatal error; 2 partial file/traversal failure (also argparse usage error); 130 interrupted. JSON mode emits one result object to stdout for operations and handled fatal errors. Argparse usage and interrupt diagnostics go to stderr. Partial summaries retain at most 100 error details with path/stage; errors_truncated discloses overflow. Storage errors abort. Completed stages survive restart; rerun the command. No durable jobs or progress stream exists yet.

Summary analyzed counts files with at least one freshly computed stage; cache_hits counts files with all selected applicable stages cached; failed counts files with an ingestion/stage failure. A file can be both analyzed and failed when one stage succeeds and another fails. Per-stage counters give the exact breakdown. Technical-only standalone audio has no applicable stage and is indexed without fabricated analysis. A failed forced run can coexist with a prior successful result; inspect failures and analyzed_at.

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
| MP3, AAC, WAV, FLAC, M4A, OGG, Opus, AIFF/AIF, WMA | ffprobe metadata; standalone audio technical/transcript stages are not yet implemented |

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

Install and stage the local models deliberately as described in [integration setup](INTEGRATIONS.md#local-ai-setup-m3). Inference is opt-in; `--depth` is valid only with all/semantic analysis, never metadata/technical. Standalone audio remains metadata-only. FAST performs embeddings without captions. STANDARD and DEEP run independently cached `semantic_embeddings` and `semantic_vision` stages; a vision failure does not erase embeddings or technical results.

| Depth | Image | Video scene representatives | Tasks |
| --- | --- | --- | --- |
| fast | First decoded image frame | Middle scene representative | Image embeddings |
| standard | First decoded image frame | Up to eight uniformly spaced scene indices, including first/last when multiple | Image embeddings and captions |
| deep | First decoded image frame | Every detected scene representative, bounded by max_scenes | Image embeddings and captions |

This is semantic coverage, not a guarantee that FAST avoids the full video technical pass. Video semantics requires successful current-version technical analysis and its JPEGs, preserving source PTS/time-base evidence; a failed forced technical pass cannot silently fall back to old frames. Coverage discloses selected/available counts. Captions attach scene index, scene bounds and actual representative-frame timing; still evidence identifies the image. No temporal reasoning, face identity, speech or event grouping is claimed. Derived video frames retain M2 HDR/rotation limits.

Image preprocessing corrects EXIF orientation, composites alpha on white, bounds source pixels to50 million and longest side to1024, and removes metadata before inference. CLIP/SigLIP applies its installed processor afterwards; color is not ICC-managed. Model weights/tokenizer/processor content, runtime versions, depth, task, prompt/options and video dependency key form semantic cache identity. Changing vision does not invalidate embeddings or metadata; changing embedding content/processing selects a distinct space. Mutating a loaded model fails explicitly; restart with an immutable snapshot.

`search --text ...` embeds language with the same encoder as stored images; `search --image PATH` embeds the supplied image without indexing it. Select the same --depth used for analysis. Only compatible space/depth/algorithm results are compared; no cross-model projection or caption-keyword fallback occurs. Empty compatible indexes return an empty items array. Search retains historical indexed media even if source files have since moved/disappeared; use show and re-scan before acting. Same-space dimension corruption fails, rather than producing a misleading similarity.

Ranking uses exact normalized cosine similarity, maximum selected-frame score per media; ties sort by content ID. Scores in [-1,1] are not probabilities or quality/storytelling scores. A bounded top-N result is returned, without a cursor; execution scans compatible stored vectors linearly (no approximate index or large-NAS performance claim). Evidence comes from the winning frame. Captions are untrusted `model_hypothesis` with null confidence and may hallucinate even simple details; do not execute model text as agent instructions.

Encoder loading/inference is synchronous on CPU without an enforceable per-call deadline. semantic_timeout (default 120 seconds) bounds each Ollama HTTP request; responses are capped at 4 MiB and captions/text inputs at 32768 characters. Text is tokenized with truncation at the installed model's token limit. No retries occur. Ctrl-C exits; completed stages remain cached, but a multi-frame semantic stage publishes only when all selected frames succeed. No frame-level durable job checkpoint exists before M5.

## Limits, hardware and NAS operation

Default analysis uses no model/GPU; optional semantics adds local CPU encoder/model memory and an external loopback vision daemon. Processing is sequential; hashes read 1 MiB chunks. Image decoding may use substantial memory up to its source-pixel limit. Video permits <=16,777,216 source pixels, 1..32 audio channels, and max_scenes=256 by default (configurable). Scene-limit exceedance fails explicitly, never returns silently truncated complete analysis. Technical subprocesses have finite timeouts (default 30 seconds); raise timeout for long clips/NAS reads. Technical probe/error/JPEG payloads are bounded and timeline/audio output streamed. Metadata-only probing retains its original uncapped capture behavior; embedded metadata can still be large.

Video timeline/cut passes decode content, not just container headers. Scene JPEGs use seeking and small derivatives. No general throughput/RAM floor is established; benchmark your codecs/NAS. Exclude @eaDir and snapshots. Default full hashing is I/O-expensive but detects same-size, same-mtime replacement; trust_stat explicitly weakens that guarantee. Use snapshots/quiescent libraries for consistency.

WAL requires local coordination; do not share the database across hosts on network storage. Use SQLite backup facilities or copy only while no StoryLens process is active, including artifacts as desired. Page limits bound record count, not total embedded metadata bytes. Source locations remain historical observations and availability must be checked before editing. There is no garbage-collection command yet.
