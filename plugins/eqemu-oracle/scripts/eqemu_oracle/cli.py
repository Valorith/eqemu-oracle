from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

from .constants import CACHE_ROOT, MODE_CHOICES, PLUGIN_ROOT, REPO_ROOT, SCOPE_CHOICES
from .extensions import ExtensionValidationError
from .installer import install_global_plugin
from .mcp import McpServer, serve_mcp
from .operations import prune_schema_extensions_dataset, rebuild_extensions_dataset, refresh_dataset
from .remote import (
    DEFAULT_MAX_DOWNLOAD_BYTES,
    EQEMU_REMOTE_MAP_SCOPES,
    RemoteConfigError,
    RemoteSecretError,
    RemoteTransferError,
    WRITE_HISTORY_LIMIT,
    WRITE_HISTORY_MAX_BYTES,
    configure_profile_from_env,
    delete_remote_file,
    garbage_collect_write_history,
    interactive_setup,
    map_remote_eqemu_server,
    list_profiles as list_remote_profiles,
    list_remote_files,
    list_write_history,
    onboarding_payload as remote_onboarding_payload,
    preview_write_undo,
    remove_profile as remove_remote_profile,
    set_profile_read_only_mode,
    stage_remote_file,
    test_connection as test_remote_connection,
    trust_ftps_certificate,
    undo_write_operation,
    upload_staged_file,
    upload_staged_file_write_session,
)
from .release_bundle import build_release_bundle
from .updater import update_plugin_repo

READ_TOOL_NAMES = (
    "search_eqemu_context",
    "get_quest_api_entry",
    "get_quest_api_overloads",
    "summarize_quest_api_topic",
    "get_db_table",
    "explain_db_relationships",
    "get_doc_page",
    "explain_eqemu_provenance",
    "get_eqemu_example_file",
)


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


def remote_onboarding(args: argparse.Namespace) -> int:
    _ = args
    print(json.dumps(remote_onboarding_payload(), indent=2, sort_keys=True))
    return 0


def remote_setup(args: argparse.Namespace) -> int:
    if args.password_env:
        if not args.host or not args.username:
            print("--host and --username are required when --password-env is used.", file=sys.stderr)
            return 2
        result = configure_profile_from_env(
            name=args.profile or "default",
            protocol=args.protocol or "ftps",
            host=args.host,
            port=args.port,
            username=args.username,
            root_path=args.root_path or "/",
            passive=not bool(args.active),
            verify_tls=not bool(args.no_verify_tls),
            allow_insecure=bool(args.allow_insecure),
            password_env_var=args.password_env,
            overwrite=bool(args.overwrite),
            test_before_save=not bool(args.no_test),
        )
    else:
        result = interactive_setup(args)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def remote_profiles(args: argparse.Namespace) -> int:
    _ = args
    print(json.dumps(list_remote_profiles(), indent=2, sort_keys=True))
    return 0


def remote_remove(args: argparse.Namespace) -> int:
    print(json.dumps(remove_remote_profile(args.profile), indent=2, sort_keys=True))
    return 0


def remote_read_only(args: argparse.Namespace) -> int:
    result = set_profile_read_only_mode(
        args.profile,
        read_only=bool(args.enable),
        confirm_mode_change=bool(args.confirm_mode_change),
        confirm_profile=args.confirm_profile,
        confirm_read_only_mode=args.confirm_read_only_mode,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def remote_test(args: argparse.Namespace) -> int:
    print(json.dumps(test_remote_connection(args.profile), indent=2, sort_keys=True))
    return 0


def remote_trust_cert(args: argparse.Namespace) -> int:
    result = trust_ftps_certificate(
        args.profile,
        confirm_trust=bool(args.confirm_trust),
        confirm_sha256=args.confirm_sha256,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def remote_list(args: argparse.Namespace) -> int:
    result = list_remote_files(
        args.profile,
        remote_path=args.remote_path,
        recursive=bool(args.recursive),
        limit=args.limit,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def remote_map(args: argparse.Namespace) -> int:
    result = map_remote_eqemu_server(
        args.profile,
        remote_path=args.remote_path,
        scope=args.scope,
        zone=args.zone,
        max_depth=args.max_depth,
        limit=args.limit,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def remote_stage(args: argparse.Namespace) -> int:
    result = stage_remote_file(
        args.profile,
        remote_path=args.remote_path,
        overwrite_policy=args.overwrite_policy,
        max_bytes=args.max_bytes,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def remote_upload(args: argparse.Namespace) -> int:
    result = upload_staged_file(
        args.profile,
        local_path=str(args.local_path),
        remote_path=args.remote_path,
        confirm_write=bool(args.confirm_write),
        confirm_remote_path=args.confirm_remote_path,
        confirm_remote_sha256=args.confirm_remote_sha256,
        create_backup=True,
        allow_create=bool(args.allow_create),
        allow_remote_changed=bool(args.allow_remote_changed),
        max_backup_bytes=args.max_backup_bytes,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def remote_upload_session(args: argparse.Namespace) -> int:
    result = upload_staged_file_write_session(
        args.profile,
        local_path=str(args.local_path),
        remote_path=args.remote_path,
        confirm_write=bool(args.confirm_write),
        confirm_remote_path=args.confirm_remote_path,
        confirm_remote_sha256=args.confirm_remote_sha256,
        confirm_temporary_read_write=bool(args.confirm_temporary_read_write),
        confirm_final_read_only=bool(args.confirm_final_read_only),
        allow_create=bool(args.allow_create),
        allow_remote_changed=bool(args.allow_remote_changed),
        max_backup_bytes=args.max_backup_bytes,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def remote_delete(args: argparse.Namespace) -> int:
    result = delete_remote_file(
        args.profile,
        remote_path=args.remote_path,
        confirm_delete=bool(args.confirm_delete),
        confirm_remote_path=args.confirm_remote_path,
        confirm_remote_sha256=args.confirm_remote_sha256,
        max_backup_bytes=args.max_backup_bytes,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def remote_history(args: argparse.Namespace) -> int:
    result = list_write_history(args.profile, limit=args.limit)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def remote_gc(args: argparse.Namespace) -> int:
    result = garbage_collect_write_history(
        args.profile,
        apply=bool(args.apply),
        confirm_write=bool(args.confirm_write),
        prune_orphans=not bool(args.no_prune_orphans),
        max_operations=args.max_operations,
        max_bytes=args.max_bytes,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def remote_undo_preview(args: argparse.Namespace) -> int:
    result = preview_write_undo(
        args.profile,
        operation_id=args.operation_id,
        check_remote=not bool(args.no_check_remote),
        max_bytes=args.max_bytes,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def remote_undo(args: argparse.Namespace) -> int:
    result = undo_write_operation(
        args.profile,
        operation_id=args.operation_id,
        confirm_write=bool(args.confirm_write),
        confirm_operation_id=args.confirm_operation_id,
        allow_remote_changed=bool(args.allow_remote_changed),
        max_bytes=args.max_bytes,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def list_tools(args: argparse.Namespace) -> int:
    server = McpServer()
    print(json.dumps(server._tool_spec(), indent=2, sort_keys=True))
    return 0


def run_tool(args: argparse.Namespace) -> int:
    try:
        tool_args = json.loads(args.args)
    except json.JSONDecodeError as exc:
        print(f"Invalid JSON passed to --args: {exc}", file=sys.stderr)
        return 2
    if not isinstance(tool_args, dict):
        print("Invalid --args value: expected a JSON object.", file=sys.stderr)
        return 2

    server = McpServer()
    try:
        result = server._handle_tool(args.name, tool_args)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.markdown:
        content = result.get("content", [])
        if content and isinstance(content[0], dict):
            print(str(content[0].get("text", "")))
        return 0

    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


def run_hook(args: argparse.Namespace) -> int:
    hook_path = PLUGIN_ROOT / "hooks" / "eqemu_oracle_hooks.py"
    spec = importlib.util.spec_from_file_location("eqemu_oracle_hooks", hook_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load hook script: {hook_path}")
    module = importlib.util.module_from_spec(spec)
    old_argv = sys.argv
    try:
        sys.argv = [str(hook_path), args.mode]
        spec.loader.exec_module(module)
        return int(module.main())
    finally:
        sys.argv = old_argv


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
        help="Install or refresh the global Codex plugin copy in the stable local marketplace",
    )
    install_parser.set_defaults(func=install_global)

    build_bundle_parser = subparsers.add_parser("build-release-bundle", help="Create a versioned release zip from the current repository state")
    build_bundle_parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "dist")
    build_bundle_parser.set_defaults(func=build_bundle)

    remote_parser = subparsers.add_parser("remote", help="Manage remote EQEmu server FTP/FTPS profiles and staged files")
    remote_subparsers = remote_parser.add_subparsers(dest="remote_command", required=True)

    remote_onboarding_parser = remote_subparsers.add_parser("onboarding", help="Show the safe FTP/FTPS onboarding flow")
    remote_onboarding_parser.set_defaults(func=remote_onboarding)

    remote_setup_parser = remote_subparsers.add_parser("setup", help="Interactively create or update a remote EQEmu server FTP/FTPS profile")
    remote_setup_parser.add_argument("--profile", help="Profile name; defaults to 'default'")
    remote_setup_parser.add_argument("--protocol", choices=("ftps", "ftp"))
    remote_setup_parser.add_argument("--host")
    remote_setup_parser.add_argument("--port", type=int)
    remote_setup_parser.add_argument("--username")
    remote_setup_parser.add_argument("--root-path")
    remote_setup_parser.add_argument("--active", action="store_true", help="Use active FTP mode instead of passive mode")
    remote_setup_parser.add_argument("--no-verify-tls", action="store_true", help="Do not verify FTPS certificates")
    remote_setup_parser.add_argument("--allow-insecure", action="store_true", help="Allow plain FTP transport")
    remote_setup_parser.add_argument("--password-env", help="Read password from a local environment variable instead of prompting")
    remote_setup_parser.add_argument("--overwrite", action="store_true", help="Replace an existing profile with the same name")
    remote_setup_parser.add_argument("--no-test", action="store_true", help="Save without testing the connection first")
    remote_setup_parser.set_defaults(func=remote_setup)

    remote_profiles_parser = remote_subparsers.add_parser("profiles", help="List configured remote profiles")
    remote_profiles_parser.set_defaults(func=remote_profiles)

    remote_remove_parser = remote_subparsers.add_parser("remove", help="Remove a configured remote profile and its stored credential")
    remote_remove_parser.add_argument("profile")
    remote_remove_parser.set_defaults(func=remote_remove)

    remote_read_only_parser = remote_subparsers.add_parser("read-only", help="Preview or set a profile's FTP read-only mode")
    remote_read_only_parser.add_argument("profile")
    remote_read_only_state = remote_read_only_parser.add_mutually_exclusive_group(required=True)
    remote_read_only_state.add_argument("--enable", action="store_true", help="Set this FTP profile to read-only mode")
    remote_read_only_state.add_argument("--disable", action="store_true", help="Set this FTP profile to read-write mode")
    remote_read_only_parser.add_argument("--confirm-mode-change", action="store_true")
    remote_read_only_parser.add_argument("--confirm-profile")
    remote_read_only_parser.add_argument("--confirm-read-only-mode", choices=("read-only", "read-write"))
    remote_read_only_parser.set_defaults(func=remote_read_only)

    remote_test_parser = remote_subparsers.add_parser("test", help="Test a configured remote profile")
    remote_test_parser.add_argument("profile")
    remote_test_parser.set_defaults(func=remote_test)

    remote_trust_cert_parser = remote_subparsers.add_parser("trust-cert", help="Preview or pin the currently presented FTPS certificate")
    remote_trust_cert_parser.add_argument("profile")
    remote_trust_cert_parser.add_argument("--confirm-trust", action="store_true")
    remote_trust_cert_parser.add_argument("--confirm-sha256")
    remote_trust_cert_parser.set_defaults(func=remote_trust_cert)

    remote_list_parser = remote_subparsers.add_parser("list", help="List files under a configured remote profile root")
    remote_list_parser.add_argument("profile")
    remote_list_parser.add_argument("--remote-path", default=".")
    remote_list_parser.add_argument("--recursive", action="store_true")
    remote_list_parser.add_argument("--limit", type=int, default=100)
    remote_list_parser.set_defaults(func=remote_list)

    remote_map_parser = remote_subparsers.add_parser("map", help="Map and classify a remote EQEmu server directory layout")
    remote_map_parser.add_argument("profile")
    remote_map_parser.add_argument("--remote-path")
    remote_map_parser.add_argument("--scope", choices=EQEMU_REMOTE_MAP_SCOPES, default="auto")
    remote_map_parser.add_argument("--zone", help="Zone short name for --scope zone")
    remote_map_parser.add_argument("--max-depth", type=int)
    remote_map_parser.add_argument("--limit", type=int, default=1000)
    remote_map_parser.set_defaults(func=remote_map)

    remote_stage_parser = remote_subparsers.add_parser("stage", help="Download one remote file into the local staging area")
    remote_stage_parser.add_argument("profile")
    remote_stage_parser.add_argument("remote_path")
    remote_stage_parser.add_argument("--overwrite-policy", choices=("versioned", "overwrite", "fail"), default="versioned")
    remote_stage_parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_DOWNLOAD_BYTES)
    remote_stage_parser.set_defaults(func=remote_stage)

    remote_upload_parser = remote_subparsers.add_parser("upload", help="Upload a staged file back to the remote server after explicit confirmation")
    remote_upload_parser.add_argument("profile")
    remote_upload_parser.add_argument("local_path", type=Path)
    remote_upload_parser.add_argument("--remote-path")
    remote_upload_parser.add_argument("--confirm-write", action="store_true")
    remote_upload_parser.add_argument("--confirm-remote-path")
    remote_upload_parser.add_argument("--confirm-remote-sha256")
    remote_upload_parser.add_argument("--allow-create", action="store_true")
    remote_upload_parser.add_argument("--allow-remote-changed", action="store_true")
    remote_upload_parser.add_argument("--max-backup-bytes", type=int, default=DEFAULT_MAX_DOWNLOAD_BYTES)
    remote_upload_parser.set_defaults(func=remote_upload)

    remote_upload_session_parser = remote_subparsers.add_parser(
        "upload-session",
        help="Run one approved upload, temporarily opening write access if needed and restoring read-only mode afterward",
    )
    remote_upload_session_parser.add_argument("profile")
    remote_upload_session_parser.add_argument("local_path", type=Path)
    remote_upload_session_parser.add_argument("--remote-path")
    remote_upload_session_parser.add_argument("--confirm-write", action="store_true")
    remote_upload_session_parser.add_argument("--confirm-remote-path")
    remote_upload_session_parser.add_argument("--confirm-remote-sha256")
    remote_upload_session_parser.add_argument("--confirm-temporary-read-write", action="store_true")
    remote_upload_session_parser.add_argument("--confirm-final-read-only", action="store_true")
    remote_upload_session_parser.add_argument("--allow-create", action="store_true")
    remote_upload_session_parser.add_argument("--allow-remote-changed", action="store_true")
    remote_upload_session_parser.add_argument("--max-backup-bytes", type=int, default=DEFAULT_MAX_DOWNLOAD_BYTES)
    remote_upload_session_parser.set_defaults(func=remote_upload_session)

    remote_delete_parser = remote_subparsers.add_parser("delete", help="Delete one remote file after exact confirmation and a local restore-point backup")
    remote_delete_parser.add_argument("profile")
    remote_delete_parser.add_argument("remote_path")
    remote_delete_parser.add_argument("--confirm-delete", action="store_true")
    remote_delete_parser.add_argument("--confirm-remote-path")
    remote_delete_parser.add_argument("--confirm-remote-sha256")
    remote_delete_parser.add_argument("--max-backup-bytes", type=int, default=DEFAULT_MAX_DOWNLOAD_BYTES)
    remote_delete_parser.set_defaults(func=remote_delete)

    remote_history_parser = remote_subparsers.add_parser("history", help="List recent remote write restore points")
    remote_history_parser.add_argument("profile")
    remote_history_parser.add_argument("--limit", type=int, default=25)
    remote_history_parser.set_defaults(func=remote_history)

    remote_gc_parser = remote_subparsers.add_parser("gc", help="Preview or apply local cleanup of remote write restore points")
    remote_gc_parser.add_argument("profile")
    remote_gc_parser.add_argument("--apply", action="store_true")
    remote_gc_parser.add_argument("--confirm-write", action="store_true")
    remote_gc_parser.add_argument("--keep-operations", "--max-operations", dest="max_operations", type=int, default=WRITE_HISTORY_LIMIT)
    remote_gc_parser.add_argument("--max-bytes", type=int, default=WRITE_HISTORY_MAX_BYTES)
    remote_gc_parser.add_argument("--no-prune-orphans", action="store_true")
    remote_gc_parser.set_defaults(func=remote_gc)

    remote_undo_preview_parser = remote_subparsers.add_parser("undo-preview", help="Preview restoring a remote write from a local restore point")
    remote_undo_preview_parser.add_argument("profile")
    remote_undo_preview_parser.add_argument("operation_id")
    remote_undo_preview_parser.add_argument("--no-check-remote", action="store_true")
    remote_undo_preview_parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_DOWNLOAD_BYTES)
    remote_undo_preview_parser.set_defaults(func=remote_undo_preview)

    remote_undo_parser = remote_subparsers.add_parser("undo", help="Restore a remote write from a local restore point after explicit confirmation")
    remote_undo_parser.add_argument("profile")
    remote_undo_parser.add_argument("operation_id")
    remote_undo_parser.add_argument("--confirm-write", action="store_true")
    remote_undo_parser.add_argument("--confirm-operation-id")
    remote_undo_parser.add_argument("--allow-remote-changed", action="store_true")
    remote_undo_parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_DOWNLOAD_BYTES)
    remote_undo_parser.set_defaults(func=remote_undo)

    tools_parser = subparsers.add_parser("tools", help="List EQEmu Oracle MCP tool specs as JSON")
    tools_parser.set_defaults(func=list_tools)

    tool_parser = subparsers.add_parser("tool", help="Run a read-only EQEmu Oracle MCP tool from the CLI")
    tool_parser.add_argument("name", choices=READ_TOOL_NAMES)
    tool_parser.add_argument("--args", default="{}", help="Tool arguments as a JSON object")
    tool_parser.add_argument("--markdown", action="store_true", help="Print only the presentation markdown text")
    tool_parser.set_defaults(func=run_tool)

    serve_parser = subparsers.add_parser("mcp-serve", help="Run the stdio MCP server")
    serve_parser.set_defaults(func=serve_mcp)

    hook_parser = subparsers.add_parser("hook", help=argparse.SUPPRESS)
    hook_parser.add_argument("mode", choices=("stop", "post-tool-use"))
    hook_parser.set_defaults(func=run_hook)

    args = parser.parse_args()
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        return int(args.func(args))
    except ExtensionValidationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (RemoteConfigError, RemoteSecretError, RemoteTransferError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
