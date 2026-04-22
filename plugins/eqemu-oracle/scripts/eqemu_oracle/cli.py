from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .constants import CACHE_ROOT, MODE_CHOICES, REPO_ROOT, SCOPE_CHOICES
from .extensions import ExtensionValidationError
from .installer import install_global_plugin
from .mcp import serve_mcp
from .operations import prune_schema_extensions_dataset, rebuild_extensions_dataset, refresh_dataset
from .release_bundle import build_release_bundle
from .updater import update_plugin_repo


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
    manifest = refresh_dataset(scope=args.scope, mode=args.mode)
    _print_schema_extension_health(manifest)
    return 0


def rebuild_extensions(args: argparse.Namespace) -> int:
    manifest = rebuild_extensions_dataset(scope=args.scope, mode=args.mode)
    _print_schema_extension_health(manifest)
    return 0


def prune_schema_extensions(args: argparse.Namespace) -> int:
    result, manifest = prune_schema_extensions_dataset(apply=bool(args.apply), mode=args.mode)
    print(json.dumps(result, indent=2, sort_keys=True))
    if manifest is not None:
        _print_schema_extension_health(manifest)
    return 0


def update_plugin(args: argparse.Namespace) -> int:
    result = update_plugin_repo(
        remote=args.remote,
        branch=args.branch,
        allow_dirty=args.allow_dirty,
        skip_rebuild=args.skip_rebuild,
        restore_branch=args.restore_branch,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def install_global(args: argparse.Namespace) -> int:
    result = install_global_plugin()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def build_bundle(args: argparse.Namespace) -> int:
    archive_path = build_release_bundle(output_dir=args.output_dir)
    print(json.dumps({"archive_path": str(archive_path.resolve())}, indent=2, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="EQEmu Oracle plugin runtime")
    subparsers = parser.add_subparsers(dest="command", required=True)

    refresh_parser = subparsers.add_parser("refresh", help="Refresh upstream data and rebuild merged datasets")
    refresh_parser.add_argument("--scope", choices=SCOPE_CHOICES, default="all")
    refresh_parser.add_argument("--mode", choices=MODE_CHOICES, default="committed")
    refresh_parser.set_defaults(func=refresh)

    rebuild_parser = subparsers.add_parser("rebuild-extensions", help="Rebuild merged data from base snapshots plus overlays")
    rebuild_parser.add_argument("--scope", choices=SCOPE_CHOICES, default="all")
    rebuild_parser.add_argument("--mode", choices=MODE_CHOICES, default="committed")
    rebuild_parser.set_defaults(func=rebuild_extensions)

    prune_parser = subparsers.add_parser(
        "prune-stale-schema-extensions",
        help="Preview or remove schema extension entries that now appear to be covered by upstream schema data",
    )
    prune_parser.add_argument("--apply", action="store_true", help="Remove the stale schema extension entries from their JSON files")
    prune_parser.add_argument("--mode", choices=MODE_CHOICES, default="committed")
    prune_parser.set_defaults(func=prune_schema_extensions)

    update_parser = subparsers.add_parser("update-plugin", help="Pull the plugin repo from Git and rebuild committed merged data")
    update_parser.add_argument("--remote", default="origin")
    update_parser.add_argument("--branch")
    update_parser.add_argument("--allow-dirty", action="store_true")
    update_parser.add_argument("--skip-rebuild", action="store_true")
    update_parser.add_argument("--restore-branch", action="store_true")
    update_parser.set_defaults(func=update_plugin)

    install_parser = subparsers.add_parser(
        "install",
        help="Install or refresh the global Codex plugin copy, preferring the desktop marketplace under ~/.codex/.tmp/plugins",
    )
    install_parser.set_defaults(func=install_global)

    build_bundle_parser = subparsers.add_parser("build-release-bundle", help="Create a versioned release zip from the current repository state")
    build_bundle_parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "dist")
    build_bundle_parser.set_defaults(func=build_bundle)

    serve_parser = subparsers.add_parser("mcp-serve", help="Run the stdio MCP server")
    serve_parser.set_defaults(func=serve_mcp)

    args = parser.parse_args()
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        return int(args.func(args))
    except ExtensionValidationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
