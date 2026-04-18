from __future__ import annotations

import argparse
import shutil

from .constants import BASE_ROOT, CACHE_ROOT, MERGED_ROOT, OVERLAY_ROOT
from .dataset import write_merged_dataset
from .ingest import write_base_dataset
from .mcp import serve_mcp


def refresh(args: argparse.Namespace) -> int:
    target_root = BASE_ROOT if args.mode == "committed" else OVERLAY_ROOT / "base"
    merged_root = MERGED_ROOT if args.mode == "committed" else OVERLAY_ROOT / "merged"
    if args.mode == "committed":
        if BASE_ROOT.exists():
            shutil.rmtree(BASE_ROOT)
        if MERGED_ROOT.exists():
            shutil.rmtree(MERGED_ROOT)
    write_base_dataset(target_root, scope=args.scope)
    write_merged_dataset(target_root, merged_root)
    return 0


def rebuild_extensions(args: argparse.Namespace) -> int:
    target_base = BASE_ROOT if args.mode == "committed" else OVERLAY_ROOT / "base"
    target_merged = MERGED_ROOT if args.mode == "committed" else OVERLAY_ROOT / "merged"
    write_merged_dataset(target_base, target_merged)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="EQEmu Oracle plugin runtime")
    subparsers = parser.add_subparsers(dest="command", required=True)

    refresh_parser = subparsers.add_parser("refresh", help="Refresh upstream data and rebuild merged datasets")
    refresh_parser.add_argument("--scope", choices=["all", "quest-api", "schema", "docs"], default="all")
    refresh_parser.add_argument("--mode", choices=["committed", "overlay"], default="committed")
    refresh_parser.set_defaults(func=refresh)

    rebuild_parser = subparsers.add_parser("rebuild-extensions", help="Rebuild merged data from base snapshots plus overlays")
    rebuild_parser.add_argument("--scope", choices=["all", "quest-api", "schema", "docs"], default="all")
    rebuild_parser.add_argument("--mode", choices=["committed", "overlay"], default="committed")
    rebuild_parser.set_defaults(func=rebuild_extensions)

    serve_parser = subparsers.add_parser("mcp-serve", help="Run the stdio MCP server")
    serve_parser.set_defaults(func=serve_mcp)

    args = parser.parse_args()
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    return int(args.func(args))
