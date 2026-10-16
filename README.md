# StoryLens

Local-first, editor-independent media intelligence. MIT licensed; free to self-host.

Current package: 0.6.0 — M1–M6: indexing, technical analysis, local semantics, audio/transcripts, an agent API, and coarse event grouping with durable corrections. StoryLens indexes referenced media, preserves metadata, measures image/audio signals, finds duplicate candidates, extracts timestamped video scenes, and groups stored evidence into reviewable event candidates. Opt-in local models provide image/text retrieval, captions and transcription. Durable local jobs and an optional MCP stdio server share the same service as the CLI. No automatic model downloads, cloud fallback, telemetry, subscriptions or accounts. Default analysis remains inference-free.

Storyboards and editor adapters remain **planned, not implemented**. See [architecture](ARCHITECTURE.md), [milestones](docs/IMPLEMENTATION_PLAN.md), and primary-source research for [components](docs/research/media-ecosystem.md), [existing applications](docs/research/existing-applications.md), and [FilmCraft/MCP](docs/research/filmcraft-mcp.md).

## Installation

Requires Python 3.11+, Pillow and NumPy. Install a trusted FFmpeg build for audio/video probing and video technical analysis; both ffprobe and ffmpeg must be on PATH or explicitly configured. Image-only processing needs neither binary. FFmpeg is not bundled or downloaded by StoryLens.

From a source checkout on Linux/macOS:

~~~sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
storylens --help
~~~

Windows PowerShell: use py -3 -m venv .venv, then .venv\Scripts\Activate.ps1 and the same pip commands. If Linux venv reports missing ensurepip, install your distribution's Python venv package. No PyPI release or signed desktop installer is claimed yet.

Optional HEIF support: python -m pip install -e '.[heif]'. Native codecs and camera support vary; see [formats](docs/USAGE.md#media-support).

## Quick start

Global options precede the command. Keep the catalog local even when media lives on a NAS:

~~~sh
storylens scan /mounted/media
storylens --exclude '@eaDir' analyze /mounted/media
storylens --json list --kind video --limit 20
storylens --json show 'sha256:ID_FROM_LIST'
storylens --json duplicates --mode exact
storylens --json duplicates --mode near --distance 6 --limit 20
storylens --json scenes 'sha256:VIDEO_ID' --limit 20
storylens --json events --limit 20
storylens --json feedback 'sha256:ID_FROM_LIST' --set '{"selection":"include"}'
storylens --json event-create 'User title'
storylens export --limit 50 > analysis-page.json
~~~

Use --database /absolute/local/catalog.sqlite3 consistently when overriding the platform default. Source files are referenced and read, never copied or edited. analyze runs metadata plus applicable image/video/audio technical stages; analyze PATH --stage metadata retains metadata-only processing. Audio transcription is opt-in via an installed local model and --stage transcript; timestamped JSON/SRT/VTT imports work without models.

Rerun after interruption: completed stages remain cached. --force recomputes only selected stages. Same bytes at a new path reuse analysis. Default scans hash every file, including repeat scans; --trust-stat reduces NAS reads but may miss changes preserving size/mtime. --no-trust-stat overrides a configured stat-trust setting.

## Technical results are not storytelling judgments

Image sharpness/blur, exposure, contrast and noise are derivative-based proxies. Near-duplicates use perceptual hash, color and aspect ratio, not semantic understanding. Video cuts use actual decoded source timestamps; black/frozen/motion/shakiness proxies cover a bounded frame sample. Audio levels/silence/clipping are signal measurements, not speech/music understanding. Nothing automatically excludes imperfect footage or claims emotional relevance.

Scene JPEGs live in a catalog-adjacent .artifacts directory. Missing JPEGs cause only the video technical stage to rerun. Results disclose preprocessing, timing, thresholds and limits. HDR previews are 8-bit conversions without tone mapping; do not use their appearance or measurements as HDR fidelity evidence.

## Optional local semantics

Install the optional runtime with `python -m pip install -e ".[semantic]"`; stage a trusted local Transformers CLIP/SigLIP model directory separately. STANDARD/DEEP additionally require an installed Ollama vision model and a daemon configured with `OLLAMA_NO_CLOUD=1`, restarted, whose `/api/status` reports cloud disabled. [Setup and limits](docs/INTEGRATIONS.md#local-ai-setup-m3).

~~~sh
storylens --embedding-model /models/siglip analyze /mounted/media --depth fast
storylens --embedding-model /models/siglip --json search --depth fast --text "children playing on a beach"
storylens --embedding-model /models/siglip --json search --depth fast --image /queries/reference.jpg
storylens --embedding-model /models/siglip --vision-model moondream:latest analyze /mounted/media --stage semantic --depth standard
~~~

FAST embeds one representative scene; STANDARD embeds/describes up to eight uniformly selected scenes; DEEP covers every detected scene representative. A still image contributes one frame. Results retain absolute source-time evidence and disclose coverage. Search compares only the selected model/content/preprocessing space and depth; cosine scores are not probabilities or story scores. Captions are untrusted model hypotheses, not established observations.

## Reference

- [CLI, configuration, media formats, privacy and NAS operation](docs/USAGE.md)
- [JSON contract](docs/API.md) and [packaged JSON Schemas](src/storylens/schemas/)
- [AI/provider, MCP-host and optional FilmCraft boundaries](docs/INTEGRATIONS.md)
- [Security](SECURITY.md), [contributing](CONTRIBUTING.md), [changelog](CHANGELOG.md)

No HTTP listener or editor dependency is introduced. `storylens serve --allow-root /mounted/media` runs the optional local MCP stdio transport; [host setup](docs/INTEGRATIONS.md#mcp-setup-for-codex-and-claude-code) requires a trusted executable, local catalog and explicit approved roots.

## Development and verification

~~~sh
python -m unittest discover -s tests -v
~~~

The suite covers metadata/technical/audio behavior, semantic cache/evidence/ranking/privacy boundaries, durable job restart/cancellation/lease fencing, real official-SDK MCP subprocess calls, event grouping and durable feedback survival through reanalysis. CPU SigLIP inference and installed local faster-whisper transcription have separate real-model evidence; see [integration evidence](docs/INTEGRATIONS.md#verification-and-limits) for revisions and quality limits.

Linux/macOS/Windows core/media checks passed across Python 3.11/3.12 in GitHub Actions. M5–M6 are verified with real SDK subprocess, CLI parity, privacy fail-closed, snapshot invalidation and installed-wheel core/MCP smokes. This is not proof of NAS throughput, universal GPU/codec support, full RAW support, HDR fidelity, broad semantic/event accuracy or FilmCraft interoperability.
