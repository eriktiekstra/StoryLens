# AI providers, MCP hosts and editor integrations

## What is executable today

StoryLens provides local indexing, technical/audio analysis, timestamped transcript import/export, opt-in CLIP/SigLIP retrieval/Ollama frame descriptions, local transcription, durable jobs, optional MCP stdio, coarse event grouping, durable manual events, independent corrections/preferences, and editable portable storyboards with separate scores. `storylens` / `storylens wizard` opens a loopback browser page for private local technical analysis and handoff to your existing agent. StoryLens does not run that agent or call FilmCraft. No cloud credentials or automatic model downloads exist. The inference-free profile remains [examples/local.toml](../examples/local.toml).

## Local AI setup (M3)

Install `python -m pip install -e ".[semantic]"` in a Python 3.11+ environment. PyTorch CPU is the encoder baseline; select a trusted CPU build for your platform. The optional extra adds Transformers 4.x, safetensors, SentencePiece and protobuf. Model/runtime licenses are separate from StoryLens; do not bundle weights without checking them.

Stage a complete trusted Transformers **CLIP or SigLIP** directory explicitly: weights, config, processor and tokenizer files. Set `embedding_model` or `--embedding-model` to that directory, not a Hub name. Loading uses `local_files_only=True` and `trust_remote_code=False`, with no acquisition/fallback. A missing/incompatible runtime, incomplete directory or zero/nonfinite feature fails explicitly. CPU float32 and the slow processor are explicit for reproducibility. The provider fingerprints directory content and relevant library/preprocessing versions; moving identical files preserves embedding identity. Use immutable model snapshots. Changes during a run fail rather than storing results under the old identity.

For captions, separately install a trusted Ollama release and deliberately install a **local vision-capable model**. Configure the daemon with `OLLAMA_NO_CLOUD=1` and restart it. Bind it to loopback; the StoryLens endpoint must use a numeric loopback origin, e.g. `http://127.0.0.1:11434` (not `localhost`, a LAN host or a proxy). `/api/status` must report `cloud.disabled: true`; older daemons without this evidence are rejected. `/api/tags` must identify a uniquely installed model digest and `/api/show` must advertise vision without remote/cloud routing metadata. No pulls, redirects, proxy environment handling or cloud fallback occur in StoryLens. The daemon/model are trusted local software, not a sandbox; use OS egress controls for a stronger offline boundary.

~~~toml
[storylens]
database = "/absolute/local/catalog.sqlite3"
embedding_model = "/absolute/models/siglip"
vision_model = "moondream:latest"
ollama_endpoint = "http://127.0.0.1:11434"
semantic_timeout = 120.0
~~~

Pass that file with `--config`; opt in with `analyze PATH --depth fast|standard|deep`. `--stage semantic` defaults to STANDARD and runs only semantic stages plus the video technical dependency when applicable. FAST requires only embeddings; STANDARD/DEEP also require vision. Default `analyze PATH` remains metadata+technical, with no inference. [Depth, retrieval and limits](USAGE.md#local-semantics-and-retrieval).

## Verification and limits

Linux CPU inference was exercised with official `google/siglip-base-patch16-224` revision `7fd15f0689c79d79e38b1c2e2e2370a7bf2761ed` (Apache-2.0), Transformers 4.57.6 and PyTorch 2.14.1+cpu. Image/text features have 768 dimensions and unit norms; a direct encoder smoke denied every socket connection. Ollama 0.40.4 reported cloud disabled, advertised local vision capability and generated real captions with `moondream:latest` digest `55fc3abd386771e5b5d1bbcc732f3c3f4df6e9f9f08f1131f9cc27ba2d1eec5b` (Apache-2.0). Acquisition was a deliberate verification step into temporary directories, never an application behavior. [Pinned sources/setup](research/media-ecosystem.md).

An installed-CLI smoke analyzed three generated color images and a two-scene video, validated summary/export/search JSON against the schema, retrieved relevant color evidence for three text queries, matched a reference image to itself, and retained captions for scene representatives at source times 0.6 and 1.6 seconds. A final STANDARD rerun reused all four files' embeddings/captions and the video technical stage; source hashes stayed unchanged. Changing actual encoder-config content recomputed embeddings only and excluded old-space search rows. These small synthetic checks do not measure family-media relevance or caption truthfulness.

These are correctness checks, not a broad accuracy benchmark or throughput claim. Moondream invented a white border on a plain red fixture; captions remain model_hypothesis with null confidence and must not drive factual identity/event conclusions. CLIP runtime execution, accelerator performance, multilingual retrieval and broad family-media accuracy are not established. Timestamped transcript import and optional local faster-whisper transcription are implemented independently of the semantic image stages.

## Local audio and transcript setup (M4)

Transcript import accepts existing JSON/SRT/VTT without model packages. For transcription install trusted `faster-whisper` and stage a complete local model directory, including weights, config and tokenizer.json. Set audio_transcription_model (or --audio-transcription-model), audio_transcription_device=cpu/cuda, optional audio_transcription_language, and select --stage transcript. Inference is an offline-configured isolated worker; missing libraries/assets/devices fail explicitly, with no model download or GPU fallback. Python audit hooks are not a native-code/kernel network sandbox.

The direct Python AudioModelExtractor also supports diarization (pyannote.audio/Torch/torchaudio/PyYAML with entirely local model references) and sound classification (Transformers/Torch with complete local assets). They are not separate CLI stage selectors. Anonymous speaker clusters are result-local, overlaps are preserved, and sound scores are uncalibrated. Pretrained diarization/sound inference was not exercised. [Pinned audio model/runtime evidence](research/media-ecosystem.md) records the actual CPU faster-whisper smoke and limitations.

## Cloud-provider boundary

StoryLens has no cloud-provider adapter or automatic cloud fallback. Its technical browser analysis runs locally without AI inference; optional semantic/transcription APIs use explicitly configured local models. An external agent host is a separate system: even StoryLens paths, metadata, captions or transcripts returned over MCP may be forwarded to the host's provider. Review that host's configuration and obtain separate consent for provider disclosure before sending private evidence. Permission to inspect original media does not authorize uploads, and the copied prompt is not an enforcement mechanism for the host's network access.

## MCP setup for Codex and Claude Code

Install `python -m pip install -e ".[mcp]"` (official SDK `mcp>=2.3,<3`). Register the trusted installed executable, a local catalog and explicit approved media directories. For a wizard project at `/absolute/local/holiday`, use `/absolute/local/holiday/catalog.sqlite3`, not the platform-global catalog. The wizard supplies these paths in its snippet; replace the examples below with your actual executable/project/source paths. Use a non-inference config unless local model inference is deliberately authorized. MCP tools cannot change startup configuration, model paths or roots. This server uses stdio, not the wizard's loopback HTTP listener or an experimental protocol Tasks engine.

Codex `~/.codex/config.toml` or trusted-project `.codex/config.toml`:

~~~toml
[mcp_servers.storylens]
command = "/absolute/venv/bin/storylens"
args = ["--database", "/absolute/local/holiday/catalog.sqlite3", "serve", "--allow-root", "/absolute/media"]
~~~

Claude Code project `.mcp.json`:

~~~json
{"mcpServers":{"storylens":{"type":"stdio","command":"/absolute/venv/bin/storylens","args":["--database","/absolute/local/holiday/catalog.sqlite3","serve","--allow-root","/absolute/media"]}}}
~~~

Windows uses the trusted venv Scripts/storylens.exe executable and absolute Windows paths (escape backslashes in JSON). Add repeated `--allow-root` arguments for additional directories. Never point a host at an auto-downloading `pip`/model acquisition command or give it an unrestricted home-directory root. Restrict inherited environment and review disclosure of private content; StoryLens cannot prevent the agent host forwarding returned data elsewhere.

Twenty-six tools provide authorized queries, supplied transcript import/paged export, durable start/status/cancel/resume/result/list plus bounded `start_jobs`, event grouping (`list_events`, `show_event`), manual event management (`create_event`, `rename_event`), independent corrections/preferences (`get_feedback`, `set_feedback`, `reset_feedback`, `get_preferences`, `set_preferences`), and portable recommendations (`recommend_storyboard`, `validate_storyboard`). Batch submission validates all authorized roots first and returns every durable ID; full errors/state remain queryable through `job_status`. Server disconnect leaves jobs running; cancel_job records a separate cancellation request. Scenes/transcript content use dedicated pages; show/export disclose omissions and arbitrary metadata is bounded. Curation and recommendations filter unapproved media before grouping and fail closed for mixed-root manual relationships. Storyboard responses are complete or an explicit size error, never truncated selections. [API/schema contracts](API.md#mcp-stdio-envelopes-m5) document cursors and limits.

Verification used the official 2.3.0 ClientSession/stdio_client against actual subprocesses, including handshake, jobs across reconnect, schema resources, CLI parity and unauthorized/malformed input. The same smoke passed from the installed wheel outside source-package imports. Codex/Claude configuration follows their official docs, but those host applications were not launched here; [primary sources](research/filmcraft-mcp.md#m5-implementation-evidence-addendum--2026-10-16).

## Agent handoff and media consent

Configure the official [FilmCraft MCP](https://github.com/storytold/filmcraft) separately in your existing agent host, using the official instructions for your installed version. StoryLens supplies no FilmCraft adapter, native-project API or editor-write MCP tools. It does not launch the agent or FilmCraft. FilmCraft tool discovery, creative decisions, native projects, preview/render and export are owned by your agent and its configured FilmCraft server, with your review and approval.

Use the browser wizard to analyze a local collection, then copy its project-specific StoryLens server snippet into that host and its handoff prompt into your agent conversation. The host must use the same local project catalog and explicitly approved source roots. The prompt is guidance you choose to send; it does not establish a connection or transfer files.

There are two distinct consent boundaries:

- **Original-media inspection is opt-in.** The checkbox starts unchecked. Without permission, the prompt instructs the agent not to open/decode originals. You may explicitly authorize inspection of the selected collection for subjective clip selection and visual continuity. Technical proxies alone cannot determine what is meaningful, who appears, emotional relevance or whether a sequence tells a good story. Existing model captions remain hypotheses, not facts.
- **Cloud/provider disclosure requires separate approval.** Inspection permission does not authorize uploading originals, frames, audio, transcripts, metadata or private paths. If the host uses a cloud provider, approve the provider and data scope separately before disclosure. Root authorization limits StoryLens queries, not the host's other tools, and StoryLens cannot enforce the copied instruction on that host.

A handoff prompt can start without inspection permission:

~~~text
Use StoryLens MCP to review the analyzed collection in
/absolute/local/holiday/catalog.sqlite3. Do not open, decode or inspect
original media until I explicitly authorize it. Technical measurements are
evidence, not subjective storytelling judgments; explain gaps and ask for
inspection permission when needed.

Use only the official FilmCraft MCP tools advertised by my configured server.
Make creative choices with my review; do not invent tool names or capabilities.
Ask before native project changes, rendering or export. Do not upload media
or private analysis to any cloud provider without my separate approval.
~~~

If you choose to opt in, replace the inspection prohibition with an explicit authorization scoped to your source folder, such as: “I explicitly allow you to inspect the original media in `/absolute/media` for clip selection and continuity review.” Keep the separate cloud/provider restriction in place. Review the complete copied prompt before sending it.

For an agent-only workflow, the host can start and monitor StoryLens analysis jobs, page scenes/transcripts/events, apply your corrections, and request/validate portable storyboards. The host then discovers and calls its official FilmCraft tools directly. StoryLens's ordinary transcript import/export remains available for supplied JSON/SRT/VTT; no native FilmCraft project parsing is required.

## Historical FilmCraft evidence

The removed package-owned M8 adapter was exercised with FilmCraft 0.9.0 and MCP 2.3 on Linux using generated media, including new-project assembly, save/reopen, saved source transcripts and rendering/export. These results do not establish compatibility or safety of your current host/server setup. Historical tool/version mappings and their limits remain in [research](research/filmcraft-mcp.md#m8-pinned-filmcraft-v090-source-mapping-addendum--2026-10-18) and the [changelog](../CHANGELOG.md); they are not active package instructions.
