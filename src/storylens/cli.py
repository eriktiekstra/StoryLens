"""CLI for local indexing, technical analysis and opt-in local semantics."""

import argparse
import json
from pathlib import Path
import sqlite3
import sys

from . import __version__
from .config import load_config
from .service import Analyzer


def parser():
    cli = argparse.ArgumentParser(prog="storylens", description="StoryLens: local media analysis and semantic retrieval")
    cli.add_argument("--version", action="version", version=__version__)
    cli.add_argument("--config", type=Path, help="TOML configuration file")
    cli.add_argument("--database", type=Path, help="Local SQLite catalog path")
    cli.add_argument("--ffprobe", help="ffprobe executable")
    cli.add_argument("--ffmpeg", help="ffmpeg executable")
    cli.add_argument("--scene-threshold", type=float, help="Scene change sensitivity (0,1]")
    cli.add_argument("--minimum-scene-seconds", type=float, help="Minimum gap between candidate cuts")
    cli.add_argument("--max-scenes", type=int, help="Explicit per-video scene/artifact safety limit")
    cli.add_argument("--timeout", type=float, help="Per-subprocess timeout in seconds")
    cli.add_argument("--embedding-model", help="Installed local Transformers CLIP/SigLIP model directory")
    cli.add_argument("--vision-model", help="Installed local Ollama vision model")
    cli.add_argument("--ollama-endpoint", help="Numeric loopback Ollama URL")
    cli.add_argument("--semantic-timeout", type=float, help="Ollama HTTP request timeout in seconds")
    cli.add_argument("--max-audio-seconds", type=float, help="Maximum audio duration to decode for technical or transcription analysis")
    cli.add_argument("--audio-transcription-model", help="Local directory of an installed Faster-Whisper model")
    cli.add_argument("--audio-transcription-device", choices=("cpu", "cuda"), help="Device for local transcription inference")
    cli.add_argument("--audio-transcription-language", help="ISO language code hint for transcription, or empty for auto-detect")
    cli.add_argument("--exclude", action="append", dest="exclusions", help="Glob against relative path or basename; replaces configured exclusions")
    cli.add_argument("--trust-stat", action=argparse.BooleanOptionalAction, default=None, help="Reuse identity for matching size/mtime (weaker than hashing)")
    cli.add_argument("--json", action="store_true", help="Machine-readable versioned JSON")
    commands = cli.add_subparsers(dest="command", required=True)
    for name in ("scan", "analyze"):
        command = commands.add_parser(name, help="Index files" if name == "scan" else "Extract metadata and technical measurements")
        command.add_argument("path", type=Path)
        if name == "analyze":
            command.add_argument("--force", action="store_true", help="Recompute the selected stages even when cached")
            command.add_argument("--stage", choices=("metadata", "technical", "semantic", "transcript", "all"), default="all")
            command.add_argument("--depth", choices=("fast", "standard", "deep"), help="Opt into semantic stages; semantic-only defaults to standard")
    for name in ("list", "export"):
        command = commands.add_parser(name, help="Page through media identities" if name == "list" else "Export one bounded page including analysis")
        command.add_argument("--limit", type=int, default=50)
        command.add_argument("--after", default="", help="Opaque cursor from previous page")
        command.add_argument("--kind", choices=("image", "video", "audio"))
    show = commands.add_parser("show", help="Get stored metadata and file references")
    show.add_argument("media_id")
    transcript = commands.add_parser("transcript", help="Import a transcript file for a catalogued media ID")
    transcript.add_argument("--media-id", required=True, help="Media identity the transcript describes")
    transcript.add_argument("--format", choices=("json", "srt", "vtt"), help="Input transcript format (default: inferred from path)")
    transcript.add_argument("--offset", type=float, default=0, help="Seconds added to every imported timestamp")
    transcript.add_argument("--language", help="Override or supply transcript language")
    transcript.add_argument("path", type=Path, help="Transcript file to import")
    duplicates = commands.add_parser("duplicates", help="Find exact copies or conservative image near-duplicates")
    duplicates.add_argument("--mode", choices=("exact", "near"), default="exact")
    duplicates.add_argument("--distance", type=int, default=6, help="Maximum perceptual hash distance for near mode")
    for command in (duplicates, commands.add_parser("scenes", help="Page through analyzed video scenes")):
        command.add_argument("--limit", type=int, default=50)
        command.add_argument("--after", default="")
    command.add_argument("media_id")
    search = commands.add_parser("search", help="Same-space text/image cosine retrieval over stored embeddings")
    query = search.add_mutually_exclusive_group(required=True)
    query.add_argument("--text", help="Natural-language query")
    query.add_argument("--image", type=Path, help="Query image (not added to catalog)")
    search.add_argument("--depth", choices=("fast", "standard", "deep"), default="standard")
    search.add_argument("--limit", type=int, default=20, help="Top media matches, 1..500")
    serve = commands.add_parser("serve", help="Run the optional MCP stdio server")
    serve.add_argument("--allow-root", action="append", required=True, type=Path,
                       help="Approved media directory; repeat for multiple roots")
    job = commands.add_parser("job", help="Submit and manage durable local analysis jobs")
    job.add_argument("--allow-root", action="append", required=True, type=Path)
    actions = job.add_subparsers(dest="action", required=True)
    start = actions.add_parser("start", help="Return a durable ID; work outlives this command")
    start.add_argument("path", type=Path)
    start.add_argument("--scan", action="store_true", help="Index only, without analysis")
    start.add_argument("--force", action="store_true")
    start.add_argument("--stage", choices=("metadata", "technical", "semantic", "transcript", "all"), default="all")
    start.add_argument("--depth", choices=("fast", "standard", "deep"))
    for action in ("status", "cancel", "resume", "result"):
        actions.add_parser(action).add_argument("job_id")
    jobs = actions.add_parser("list")
    jobs.add_argument("--limit", type=int, default=50)
    jobs.add_argument("--after", default="")
    events = commands.add_parser("events", help="Page coarse event candidates without inference")
    events.add_argument("--limit", type=int, default=50)
    events.add_argument("--after", default="")
    event = commands.add_parser("event", help="Page member evidence of an automatic or manual event")
    event.add_argument("event_id")
    event.add_argument("--limit", type=int, default=50)
    event.add_argument("--after", default="")
    create = commands.add_parser("event-create", help="Create a stable manual event for explicit assignments")
    create.add_argument("title")
    rename = commands.add_parser("event-rename", help="Rename a user-owned manual event")
    rename.add_argument("event_id")
    rename.add_argument("title")
    feedback = commands.add_parser("feedback", help="Read or patch corrections, independent of AI data")
    feedback.add_argument("media_id")
    edit = feedback.add_mutually_exclusive_group()
    edit.add_argument("--set", dest="patch", type=json.loads, help="JSON correction patch; omitted fields stay unchanged")
    edit.add_argument("--clear", nargs="+", choices=("selection", "capture_time", "gps", "event_id", "segments"))
    edit.add_argument("--clear-all", action="store_true", help="Remove all user overrides for this identity")
    preferences = commands.add_parser("preferences", help="Read or patch collection grouping thresholds")
    preferences.add_argument("--set", dest="patch", type=json.loads, help="JSON preference patch")
    return cli


def emit(result, machine):
    if machine:
        print(json.dumps(result, ensure_ascii=True, allow_nan=False))
    elif "discovered" in result:
        print(f"Indexed {result['indexed']}/{result['discovered']}; analyzed {result['analyzed']}; cache hits {result['cache_hits']}; failed {result['failed']}")
        for error in result["errors"]:
            print(f"  {error['path']}: {error['error']}")
    elif "items" in result and ("mode" in result or "media_id" in result or "space" in result):
        print(json.dumps(result, indent=2, ensure_ascii=True, allow_nan=False))
    elif "items" in result:
        for item in result["items"]:
            print(f"{item['id']}  {item['kind']}  {item['size']} bytes")
        if result["next_cursor"]:
            print("Next cursor: " + result["next_cursor"])
    else:
        print(json.dumps(result, indent=2, ensure_ascii=True, allow_nan=False))


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        config = load_config(args.config, database=args.database, ffprobe=args.ffprobe, ffmpeg=args.ffmpeg,
                             timeout=args.timeout, exclusions=args.exclusions, trust_stat=args.trust_stat,
                             scene_threshold=args.scene_threshold, minimum_scene_seconds=args.minimum_scene_seconds, max_scenes=args.max_scenes,
                             embedding_model=args.embedding_model, vision_model=args.vision_model,
                             ollama_endpoint=args.ollama_endpoint, semantic_timeout=args.semantic_timeout,
                             max_audio_seconds=args.max_audio_seconds, audio_transcription_model=args.audio_transcription_model,
                             audio_transcription_device=args.audio_transcription_device, audio_transcription_language=args.audio_transcription_language)
        if args.command == "serve":
            try:
                from .mcp_server import main as serve
            except ImportError as error:
                raise RuntimeError('Install the MCP extra: pip install "storylens[mcp]"') from error
            serve(config, args.allow_root)
            return 0
        if args.command == "job":
            from .jobs import JobManager
            manager = JobManager(config, args.allow_root)
            if args.action == "start":
                result = manager.start(args.path, analyze=not args.scan, stage=args.stage,
                                       depth=args.depth, force=args.force)
            elif args.action == "list":
                result = manager.list(limit=args.limit, after=args.after)
            else:
                result = getattr(manager, args.action)(args.job_id)
            emit(result, True)
            return 2 if result.get("status") == "failed" else 0
        with Analyzer(config) as storylens:
            if args.command in ("scan", "analyze"):
                result = storylens.scan(args.path, analyze=args.command == "analyze", force=getattr(args, "force", False),
                                        stage=getattr(args, "stage", "all"), depth=getattr(args, "depth", None))
            elif args.command == "search":
                result = storylens.search(text=args.text, image=args.image, depth=args.depth, limit=args.limit)
            elif args.command == "duplicates":
                result = storylens.duplicates(mode=args.mode, max_distance=args.distance, limit=args.limit, after=args.after)
            elif args.command == "scenes":
                result = storylens.scenes(args.media_id, limit=args.limit, after=args.after)
            elif args.command == "events":
                result = storylens.events(limit=args.limit, after=args.after)
            elif args.command == "event":
                result = storylens.event(args.event_id, limit=args.limit, after=args.after)
            elif args.command == "event-create":
                result = storylens.create_event(args.title)
            elif args.command == "event-rename":
                result = storylens.rename_event(args.event_id, args.title)
            elif args.command == "feedback":
                if args.patch is not None:
                    result = storylens.set_feedback(args.media_id, args.patch)
                elif args.clear_all:
                    result = storylens.reset_feedback(args.media_id)
                elif args.clear is not None:
                    result = storylens.reset_feedback(args.media_id, args.clear)
                else:
                    result = storylens.feedback(args.media_id)
            elif args.command == "preferences":
                result = storylens.preferences() if args.patch is None else storylens.set_preferences(args.patch)
            elif args.command == "show":
                result = storylens.show(args.media_id)
            elif args.command == "transcript":
                result = storylens.import_transcript(args.media_id, args.path, format=args.format,
                                                     offset=args.offset, language=args.language)
                if args.format in ("srt", "vtt"):
                    from storylens.transcripts import serialize
                    print(serialize(result, args.format), end="")
                    return 0
            else:
                result = {"schema_version": "1.0", **storylens.catalog.page(args.limit, args.after, args.kind)}
                if args.command == "export":
                    result["items"] = [storylens.catalog.get(item["id"]) for item in result["items"]]
        emit(result, args.json or args.command in {"export", "events", "event", "event-create",
                                                "event-rename", "feedback", "preferences"})
        return 2 if result.get("failed") else 0
    except (OSError, ValueError, TypeError, RuntimeError, KeyError, sqlite3.Error) as error:
        if args.json and args.command != "serve":
            print(json.dumps({"schema_version": "1.0", "error": str(error)}, ensure_ascii=True))
        else:
            print("error: " + str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted; rerun to reuse completed analysis.", file=sys.stderr)
        return 130
