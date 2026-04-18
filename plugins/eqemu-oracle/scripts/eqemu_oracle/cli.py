from __future__ import annotations

import argparse
import json
import os
import stat
import shutil
import sys

from .constants import BASE_ROOT, CACHE_ROOT, MERGED_ROOT, OVERLAY_ROOT
from .dataset import prune_stale_schema_extensions, write_merged_dataset
from .extensions import ExtensionValidationError
from .ingest import write_base_dataset
from .mcp import serve_mcp
from .updater import update_plugin_repo


def _remove_tree(path) -> None:
    def onerror(func, failed_path, exc_info):  # type: ignore[no-untyped-def]
        os.chmod(failed_path, stat.S_IWRITE)
        func(failed_path)

    shutil.rmtree(path, onexc=onerror)


def _print_schema_extension_health(manifest: dict[str, object]) -> None:
    extension_health = manifest.get("extension_health")
    if not isinstance(extension_health, dict):
        return
    candidate_count = int(extension_health.get("stale_schema_candidate_count", 0))
    if candidate_count <= 0:
        return
    print(
        (
            f"Warning: {candidate_count} schema extension entr"
            f"{'y looks' if candidate_count == 1 else 'ies look'} stale because upstream schema now covers them.\n"
            "Run `prune-stale-schema-extensions` to review them or `prune-stale-schema-extensions --apply` to remove them automatically."
        ),
        file=sys.stderr,
    )


def refresh(args: argparse.Namespace) -> int:
    target_root = BASE_ROOT if args.mode == "committed" else OVERLAY_ROOT / "base"
    merged_root = MERGED_ROOT if args.mode == "committed" else OVERLAY_ROOT / "merged"
    if args.mode == "committed":
        if BASE_ROOT.exists():
            _remove_tree(BASE_ROOT)
        if MERGED_ROOT.exists():
            _remove_tree(MERGED_ROOT)
        if OVERLAY_ROOT.exists():
            _remove_tree(OVERLAY_ROOT)
    write_base_dataset(target_root, scope=args.scope)
    manifest = write_merged_dataset(target_root, merged_root)
    _print_schema_extension_health(manifest)
    return 0


def rebuild_extensions(args: argparse.Namespace) -> int:
    target_base = BASE_ROOT if args.mode == "committed" else OVERLAY_ROOT / "base"
    target_merged = MERGED_ROOT if args.mode == "committed" else OVERLAY_ROOT / "merged"
    manifest = write_merged_dataset(target_base, target_merged)
    _print_schema_extension_health(manifest)
    return 0


def prune_schema_extensions(args: argparse.Namespace) -> int:
    target_base = BASE_ROOT if args.mode == "committed" else OVERLAY_ROOT / "base"
    target_merged = MERGED_ROOT if args.mode == "committed" else OVERLAY_ROOT / "merged"
    result = prune_stale_schema_extensions(target_base, apply=bool(args.apply))
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.apply and result["removed_count"]:
        manifest = write_merged_dataset(target_base, target_merged)
        _print_schema_extension_health(manifest)
    return 0


def update_plugin(args: argparse.Namespace) -> int:
    result = update_plugin_repo(
        remote=args.remote,
        branch=args.branch,
        allow_dirty=args.allow_dirty,
        skip_rebuild=args.skip_rebuild,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
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

    prune_parser = subparsers.add_parser(
        "prune-stale-schema-extensions",
        help="Preview or remove schema extension entries that now appear to be covered by upstream schema data",
    )
    prune_parser.add_argument("--apply", action="store_true", help="Remove the stale schema extension entries from their JSON files")
    prune_parser.add_argument("--mode", choices=["committed", "overlay"], default="committed")
    prune_parser.set_defaults(func=prune_schema_extensions)

    update_parser = subparsers.add_parser("update-plugin", help="Pull the plugin repo from Git and rebuild committed merged data")
    update_parser.add_argument("--remote", default="origin")
    update_parser.add_argument("--branch")
    update_parser.add_argument("--allow-dirty", action="store_true")
    update_parser.add_argument("--skip-rebuild", action="store_true")
    update_parser.set_defaults(func=update_plugin)

    serve_parser = subparsers.add_parser("mcp-serve", help="Run the stdio MCP server")
    serve_parser.set_defaults(func=serve_mcp)

    args = parser.parse_args()
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        return int(args.func(args))
    except ExtensionValidationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
