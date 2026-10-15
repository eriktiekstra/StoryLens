# StoryLens

Local-first, editor-independent media intelligence. MIT licensed; free to self-host.

**Current release: 0.2.0 — M1 foundation and M2 technical analysis.** StoryLens indexes referenced media, preserves metadata, measures image quality, finds duplicate candidates, and extracts timestamped video scenes with representative JPEGs and initial frame/audio measurements. No inference, uploads, telemetry, model downloads, subscriptions or accounts.

Semantic search, transcription, MCP, event grouping, feedback and storyboards remain **planned, not implemented**. See [architecture](ARCHITECTURE.md), [milestones](docs/IMPLEMENTATION_PLAN.md), and primary-source research for [components](docs/research/media-ecosystem.md), [existing applications](docs/research/existing-applications.md), and [FilmCraft/MCP](docs/research/filmcraft-mcp.md).

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
storylens export --limit 50 > analysis-page.json
~~~

Use --database /absolute/local/catalog.sqlite3 consistently when overriding the platform default. Source files are referenced and read, never copied or edited. analyze runs metadata plus applicable image/video technical stages; analyze PATH --stage metadata retains metadata-only processing. --stage technical runs only applicable image/video technical stages. Standalone audio currently receives metadata, not transcription or technical audio analysis; video technical results include measurements from its first audio stream.

Rerun after interruption: completed stages remain cached. --force recomputes only selected stages. Same bytes at a new path reuse analysis. Default scans hash every file, including repeat scans; --trust-stat reduces NAS reads but may miss changes preserving size/mtime. --no-trust-stat overrides a configured stat-trust setting.

## Technical results are not storytelling judgments

Image sharpness/blur, exposure, contrast and noise are derivative-based proxies. Near-duplicates use perceptual hash, color and aspect ratio, not semantic understanding. Video cuts use actual decoded source timestamps; black/frozen/motion/shakiness proxies cover a bounded frame sample. Audio levels/silence/clipping are signal measurements, not speech/music understanding. Nothing automatically excludes imperfect footage or claims emotional relevance.

Scene JPEGs live in a catalog-adjacent .artifacts directory. Missing JPEGs cause only the video technical stage to rerun. Results disclose preprocessing, timing, thresholds and limits. HDR previews are 8-bit conversions without tone mapping; do not use their appearance or measurements as HDR fidelity evidence.

## Reference

- [CLI, configuration, media formats, privacy and NAS operation](docs/USAGE.md)
- [JSON contract](docs/API.md) and [JSON Schema](schemas/results-v1.schema.json)
- [AI/provider, MCP-host and optional FilmCraft boundaries](docs/INTEGRATIONS.md)
- [Security](SECURITY.md), [contributing](CONTRIBUTING.md), [changelog](CHANGELOG.md)

There is no HTTP API or MCP launch command yet. The importable service is storylens.service.Analyzer with storylens.config.Config; internal Python interfaces may evolve across minor releases.

## Development and verification

~~~sh
python -m unittest discover -s tests -v
~~~

51 tests pass on Linux, including real hard cuts, VFR/nonzero-origin timing, display-matrix rotation, audio silence/clipping, cache isolation, missing-artifact recovery, corruption, protocol denial and child-process cancellation. An installed-CLI smoke analyzed five read-only image/video files, found exact/reencoded candidates, produced two scene JPEGs, reused every file's selected stages on rerun, validated JSON outputs and left source hashes unchanged. A wheel builds successfully.

Windows/macOS execution, NAS throughput, full RAW support, HDR fidelity, model quality and FilmCraft runtime interoperability are not yet verified. Platform CI is configured, not evidence of an observed cross-platform run.
