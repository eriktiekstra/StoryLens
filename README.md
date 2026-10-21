# StoryLens

Local-first, editor-independent media intelligence. MIT licensed; free to self-host.

StoryLens analyzes photos, videos and audio without changing the originals. Start with basic analysis, then optionally add local AI to the same project. A guided terminal wizard and a local browser interface share saved results, settings and recovery actions. There are no accounts, subscriptions, telemetry or cloud fallback. Model downloads happen only after explicit approval.

Editable JSON storyboards work without an editor. For creative decisions and editing, the wizard supplies a StoryLens MCP configuration snippet and a prompt to copy to your existing AI host. Your agent uses StoryLens evidence and calls the official FilmCraft MCP directly; StoryLens does not run an agent, launch FilmCraft, create native editor projects or render/export movies. See [handoff and consent](docs/INTEGRATIONS.md#agent-handoff-and-media-consent), [architecture](ARCHITECTURE.md), [milestones](docs/IMPLEMENTATION_PLAN.md), and primary-source research for [components](docs/research/media-ecosystem.md), [existing applications](docs/research/existing-applications.md), and [FilmCraft/MCP](docs/research/filmcraft-mcp.md).

## Installation

You need **Python 3.11 or newer**. Videos and audio also need **FFmpeg**, which includes both `ffmpeg` and `ffprobe`. Photos in Apple HEIC/HEIF format need the optional decoder included in the installation below.

1. Install Python and FFmpeg:
   - **macOS:** with [Homebrew](https://brew.sh), run `brew install python ffmpeg`.
   - **Windows:** install Python from [python.org](https://www.python.org/downloads/), then install a trusted FFmpeg build; for example `winget install --id Gyan.FFmpeg --exact`. Reopen your terminal afterward.
   - **Ubuntu/Debian:** `sudo apt install python3 python3-venv ffmpeg`. Other Linux distributions use their own package manager.
2. Create an isolated StoryLens environment and install from this repository:

~~~sh
python3 -m venv ~/.venvs/storylens
. ~/.venvs/storylens/bin/activate
python -m pip install "storylens[heif] @ https://github.com/eriktiekstra/StoryLens/archive/refs/heads/main.zip"
storylens
~~~

On **Windows PowerShell**, use these commands instead:

~~~powershell
py -3 -m venv "$HOME\.venvs\storylens"
& "$HOME\.venvs\storylens\Scripts\Activate.ps1"
python -m pip install "storylens[heif] @ https://github.com/eriktiekstra/StoryLens/archive/refs/heads/main.zip"
storylens
~~~

Activate this environment again in a new terminal before running `storylens`. This is a repository installation, not a claimed PyPI release or signed desktop installer. If you already have a source checkout, use `python -m pip install -e '.[heif]'` from its root instead.

## Quick start

Run `storylens` to choose **Open browser** or **Continue in terminal**. You can also open either interface directly:

~~~sh
storylens wizard --web
storylens wizard --terminal
# Reopen saved results without repeating successful analysis:
storylens wizard --web --project "$HOME/storylens-projects/holiday"
~~~

Both interfaces guide you through choosing folders, checking readiness, analysis, and using the results. Choose the folder containing your mixed photos/videos, then a separate local project folder. StoryLens creates the project folder; your media stays where it is and remains unchanged.

Start with **basic analysis**, which needs no AI models. If files fail, inspect the explanation, adjust saved timeout settings or install the missing decoder, then choose retry. Successful stages are reused; you do not need to start over.

You can **add local AI later** for image descriptions, visual search, or speech transcription. The wizard explains supported models, approximate download sizes and requirements; detects reusable settings; checks setup; and offers an explicit small sample before collection processing. Model downloads and profile reuse are separate actions. AI results and failures are tracked separately from basic analysis. See the [wizard guide](docs/USAGE.md#guided-project-wizard).

The browser runs only on your computer at `127.0.0.1`; its private URL is printed in the terminal. Leave that terminal open while using the page. Both interfaces can save a private diagnostic report and open the project folder. The optional final handoff helps connect your existing agent; it does not launch the agent or FilmCraft, and permission to inspect media does not authorize cloud sharing.

### Connect your existing MCP host

Install the optional server dependency in the same environment:

~~~sh
python -m pip install -e '.[mcp]'
~~~

Copy the wizard's generated configuration into your host. For example, a project at `/absolute/local/holiday` analyzing `/absolute/media` uses:

~~~json
{
  "mcpServers": {
    "storylens": {
      "command": "/absolute/venv/bin/storylens",
      "args": ["--database", "/absolute/local/holiday/catalog.sqlite3", "serve", "--allow-root", "/absolute/media"]
    }
  }
}
~~~

Replace all paths with the actual trusted executable, project and source paths. This starts StoryLens's stdio MCP server when your host needs it; it does not start the browser or FilmCraft. Configure the official FilmCraft MCP separately in that host, then review and send the copied handoff prompt. [Codex/Claude configuration and consent](docs/INTEGRATIONS.md#mcp-setup-for-codex-and-claude-code).

### Scriptable commands

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
storylens storyboard --duration 60 --strategy balanced > storyboard.json
storylens storyboard-check storyboard.json
~~~

Use --database /absolute/local/catalog.sqlite3 consistently when overriding the platform default. Source files are referenced and read, never copied or edited. analyze runs metadata plus applicable image/video/audio technical stages; analyze PATH --stage metadata retains metadata-only processing. Audio transcription is opt-in via an installed local model and --stage transcript; timestamped JSON/SRT/VTT imports work without models.

Rerun after interruption: completed stages remain cached. --force recomputes only selected stages. Same bytes at a new path reuse analysis. Default scans hash every file, including repeat scans; --trust-stat reduces NAS reads but may miss changes preserving size/mtime. --no-trust-stat overrides a configured stat-trust setting.

## Technical results are not storytelling judgments

Image sharpness/blur, exposure, contrast and noise are derivative-based proxies. Near-duplicates use perceptual hash, color and aspect ratio, not semantic understanding. Video cuts use actual decoded source timestamps; black/frozen/motion/shakiness proxies cover a bounded frame sample. Audio levels/silence/clipping are signal measurements, not speech/music understanding. Nothing automatically excludes imperfect footage or claims emotional relevance.

Scene JPEGs live in a catalog-adjacent .artifacts directory. Missing JPEGs cause only the video technical stage to rerun. Results disclose preprocessing, timing, thresholds and limits. HDR previews are 8-bit conversions without tone mapping; do not use their appearance or measurements as HDR fidelity evidence.

## Optional local semantics

The wizard can configure local AI before analysis or enrich an existing project afterward. For manual setup from a checkout, install `python -m pip install -e ".[semantic]"` for visual search or `python -m pip install -e ".[transcription]"` for speech. Recommended-model downloading additionally needs the `model-installer` extra. Image descriptions use a separately installed local Ollama vision model and a daemon configured with `OLLAMA_NO_CLOUD=1`, restarted, whose `/api/status` reports cloud disabled. Select only the capabilities you need; they run independently. [Setup and limits](docs/INTEGRATIONS.md#local-ai-setup-m3).

~~~sh
storylens --embedding-model /models/siglip analyze /mounted/media --depth fast
storylens --embedding-model /models/siglip --json search --depth fast --text "children playing on a beach"
storylens --embedding-model /models/siglip --json search --depth fast --image /queries/reference.jpg
storylens --embedding-model /models/siglip --vision-model moondream:latest analyze /mounted/media --stage semantic --depth standard
~~~

FAST embeds one representative scene; STANDARD embeds/describes up to eight uniformly selected scenes; DEEP covers every detected scene representative. A still image contributes one frame. Results retain absolute source-time evidence and disclose coverage. Search compares only the selected model/content/preprocessing space and depth; cosine scores are not probabilities or story scores. Captions are untrusted model hypotheses, not established observations.

## Recommendations and portable storyboards

`storyboard` ranks stored evidence with configurable `balanced`, `technical` or `story` weights. It honors required/excluded identities and segment corrections, keeps transcript-timed conversation blocks whole, and never exceeds the requested duration. Missing technical or query evidence stays null; the story score is an explicit event/timing heuristic, not emotional understanding. `--text` explicitly opts into the configured local query encoder.

The output is editable JSON, not a rendered film: change title, playback order, image holds or source-time intervals, update duration totals, then run `storyboard-check`. Current feedback cannot be bypassed by editing the document. Sources remain untouched; no editor integration is required. See [constraints, scoring and limits](docs/USAGE.md#recommendations-and-editable-storyboards-m7).

## Reference

- [CLI, configuration, media formats, privacy and NAS operation](docs/USAGE.md)
- [JSON contract](docs/API.md) and [packaged JSON Schemas](src/storylens/schemas/)
- [AI/provider, MCP-host and FilmCraft handoff boundaries](docs/INTEGRATIONS.md)
- [Reproducible distribution, dependency license inventory and release gates](docs/RELEASE.md)
- [Security](SECURITY.md), [contributing](CONTRIBUTING.md), [changelog](CHANGELOG.md)

The wizard's loopback HTTP listener is separate from the optional MCP stdio transport. Install the MCP extra before registering StoryLens with your host; use the wizard's generated snippet or the [host setup examples](docs/INTEGRATIONS.md#mcp-setup-for-codex-and-claude-code), with a trusted executable, the project's local catalog and explicit approved source roots.

## Development and verification

~~~sh
python -m unittest discover -s tests -v
~~~

The suite covers metadata/technical/audio behavior, semantic cache/evidence/ranking/privacy boundaries, durable job restart/cancellation/lease fencing, real official-SDK MCP subprocess calls, event grouping, durable feedback survival, recommendation constraints and portable storyboard validation. CPU SigLIP inference and installed local faster-whisper transcription have separate real-model evidence; see [integration evidence](docs/INTEGRATIONS.md#verification-and-limits) for revisions and quality limits.

Linux/macOS/Windows core/media checks passed across Python 3.11/3.12 in GitHub Actions. M5–M6 are verified with real SDK subprocess, CLI parity, privacy fail-closed, snapshot invalidation and installed-wheel core/MCP smokes. This is not proof of NAS throughput, universal GPU/codec support, full RAW support, HDR fidelity, broad semantic/event accuracy or FilmCraft interoperability.

M7 was exercised on Linux with generated-media CLI and installed-wheel smokes, plus real MCP SDK subprocesses returning 111 lossless storyboard entries, rejecting unauthorized/mixed-root sources and failing explicitly on oversized documents. Conversation preservation is timestamp-based, not a claim of semantic accuracy; no new real-model query inference, cross-platform M7 runtime or editor export was exercised.

Historical M8 evidence: the now-removed package-owned adapter was exercised on Linux with the checksum-verified FilmCraft 0.9.0 release and MCP 2.3, including generated-media assembly, saved transcripts, rendering and export. This is not current adapter support or verification of your host's FilmCraft setup; see the [historical record](CHANGELOG.md#080--2026-10-18-m8-optional-filmcraft-interchange).

M9 adds [isolated collection benchmarks/profiles, bounded FIFO batches, conservative CUDA admission and optional Docker operation](docs/USAGE.md#release-operation-and-profiling-m9). It does not certify real NAS throughput, GPU capacity or private-media quality; those acceptance runs are deferred until implementation is complete.
