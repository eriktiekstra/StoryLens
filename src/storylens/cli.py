"""CLI for local indexing and technical analysis."""

import argparse
import json
from pathlib import Path
import sqlite3
import sys

from . import __version__
from .config import load_config
from .service import Analyzer


def parser():
    cli = argparse.ArgumentParser(prog="storylens", description="StoryLens: local metadata and technical media analysis")
    cli.add_argument("--version", action="version", version=__version__)
    cli.add_argument("--config", type=Path, help="TOML configuration file")
    cli.add_argument("--database", type=Path, help="Local SQLite catalog path")
    cli.add_argument("--ffprobe", help="ffprobe executable")
    cli.add_argument("--ffmpeg", help="ffmpeg executable")
    cli.add_argument("--scene-threshold", type=float, help="Scene change sensitivity (0,1]")
    cli.add_argument("--minimum-scene-seconds", type=float, help="Minimum gap between candidate cuts")
    cli.add_argument("--max-scenes", type=int, help="Explicit per-video scene/artifact safety limit")
    cli.add_argument("--timeout", type=float, help="Per-subprocess timeout in seconds")
    cli.add_argument("--exclude", action="append", dest="exclusions", help="Glob against relative path or basename; replaces configured exclusions")
    cli.add_argument("--trust-stat", action=argparse.BooleanOptionalAction, default=None, help="Reuse identity for matching size/mtime (weaker than hashing)")
    cli.add_argument("--json", action="store_true", help="Machine-readable versioned JSON")
    commands = cli.add_subparsers(dest="command", required=True)
    for name in ("scan", "analyze"):
        command = commands.add_parser(name, help="Index files" if name == "scan" else "Extract metadata and technical measurements")
        command.add_argument("path", type=Path)
        if name == "analyze":
            command.add_argument("--force", action="store_true", help="Recompute the selected stages even when cached")
            command.add_argument("--stage", choices=("metadata", "technical", "all"), default="all")
    for name in ("list", "export"):
        command = commands.add_parser(name, help="Page through media identities" if name == "list" else "Export one bounded page including analysis")
        command.add_argument("--limit", type=int, default=50)
        command.add_argument("--after", default="", help="Opaque cursor from previous page")
        command.add_argument("--kind", choices=("image", "video", "audio"))
    show = commands.add_parser("show", help="Get stored metadata and file references")
    show.add_argument("media_id")
    duplicates = commands.add_parser("duplicates", help="Find exact copies or conservative image near-duplicates")
    duplicates.add_argument("--mode", choices=("exact", "near"), default="exact")
    duplicates.add_argument("--distance", type=int, default=6, help="Maximum perceptual hash distance for near mode")
    for command in (duplicates, commands.add_parser("scenes", help="Page through analyzed video scenes")):
        command.add_argument("--limit", type=int, default=50)
        command.add_argument("--after", default="")
    command.add_argument("media_id")
    return cli


def emit(result, machine):
    if machine:
        print(json.dumps(result, ensure_ascii=True, allow_nan=False))
    elif "discovered" in result:
        print(f"Indexed {result['indexed']}/{result['discovered']}; analyzed {result['analyzed']}; cache hits {result['cache_hits']}; failed {result['failed']}")
        for error in result["errors"]:
            print(f"  {error['path']}: {error['error']}")
    elif "items" in result and ("mode" in result or "media_id" in result):
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
                             scene_threshold=args.scene_threshold, minimum_scene_seconds=args.minimum_scene_seconds, max_scenes=args.max_scenes)
        with Analyzer(config) as storylens:
            if args.command in ("scan", "analyze"):
                result = storylens.scan(args.path, analyze=args.command == "analyze", force=getattr(args, "force", False),
                                        stage=getattr(args, "stage", "all"))
            elif args.command == "duplicates":
                result = storylens.duplicates(mode=args.mode, max_distance=args.distance, limit=args.limit, after=args.after)
            elif args.command == "scenes":
                result = storylens.scenes(args.media_id, limit=args.limit, after=args.after)
            elif args.command == "show":
                result = {"schema_version": "1.0", "media": storylens.catalog.get(args.media_id)}
            else:
                result = {"schema_version": "1.0", **storylens.catalog.page(args.limit, args.after, args.kind)}
                if args.command == "export":
                    result["items"] = [storylens.catalog.get(item["id"]) for item in result["items"]]
        emit(result, args.json or args.command == "export")
        return 2 if result.get("failed") else 0
    except (OSError, ValueError, TypeError, sqlite3.Error) as error:
        if args.json:
            print(json.dumps({"schema_version": "1.0", "error": str(error)}, ensure_ascii=True))
        else:
            print("error: " + str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted; rerun to reuse completed analysis.", file=sys.stderr)
        return 130
