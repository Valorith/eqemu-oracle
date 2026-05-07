from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

try:
    import tomllib as _tomllib
except ModuleNotFoundError:
    try:
        import tomli as _tomllib  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        _tomllib = None

from .constants import DOMAIN_CHOICES, PLUGIN_ROOT
from .extensions import load_domain_extensions
from .utils import dump_json, ensure_dir, load_json


MARKETPLACE_NAME = "user-local"
MARKETPLACE_DISPLAY_NAME = "Local Plugins"
CODEX_DESKTOP_INSTALL_KIND = "codex-desktop-marketplace"
LEGACY_HOME_INSTALL_KIND = "legacy-home-marketplace"
CODEX_MCP_SERVER_NAME = "eqemu_oracle"
LEGACY_CODEX_MCP_SERVER_NAMES = ("eqemu-oracle",)
PRESERVED_PATHS = (
    Path("config") / "sources.local.toml",
    Path("local-extensions"),
)
COPY_IGNORE_NAMES = {
    "__pycache__",
    ".pytest_cache",
    "tests",
}
LOCAL_EXTENSION_SCAFFOLDS = {
    Path("local-extensions") / "quests" / "local.json": {
        "_instructions": (
            "Private local quest example sources go in the sources array. "
            "This file is ignored by git and preserved across installs."
        ),
        "sources": [],
        "_example_source": {
            "id": "my-local-quest-script-examples",
            "title": "My Local Quest Script Examples",
            "url": "https://github.com/example/custom-quests",
            "source_type": "github_repo",
            "context_key": "primary-quest-script-examples",
            "languages": ["perl", "lua"],
            "tags": ["quest scripts", "local examples"],
            "description": (
                "Move this object into the sources array and edit it. Keeping the default context_key "
                "replaces the repo-level ProjectEQ quest source on this machine."
            ),
            "mode": "augment",
        },
    },
    Path("local-extensions") / "plugins" / "local.json": {
        "_instructions": (
            "Private local Perl plugin example sources go in the sources array. "
            "This file is ignored by git and preserved across installs."
        ),
        "sources": [],
        "_example_source": {
            "id": "my-local-perl-plugin-examples",
            "title": "My Local Perl Plugin Examples",
            "url": "https://github.com/example/custom-plugins",
            "source_type": "github_repo",
            "context_key": "primary-perl-plugin-examples",
            "languages": ["perl"],
            "tags": ["perl plugins", "local examples"],
            "description": (
                "Move this object into the sources array and edit it. Keeping the default context_key "
                "replaces the repo-level ProjectEQ plugin source on this machine."
            ),
            "mode": "augment",
        },
    },
}


class CodexConfigError(RuntimeError):
    pass


def _load_plugin_metadata(plugin_root: Path) -> dict[str, Any]:
    metadata_path = plugin_root / ".codex-plugin" / "plugin.json"
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _plugin_name_for_source(plugin_root: Path) -> str:
    metadata = _load_plugin_metadata(plugin_root)
    name = metadata.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return plugin_root.name


def _category_for_plugin(plugin_root: Path) -> str:
    metadata = _load_plugin_metadata(plugin_root)
    if not metadata:
        return "Coding"
    interface = metadata.get("interface")
    if not isinstance(interface, dict):
        return "Coding"
    category = interface.get("category")
    if not isinstance(category, str) or not category.strip():
        return "Coding"
    return category.strip()


def _load_marketplace(path: Path) -> dict[str, Any]:
    if path.exists():
        payload = load_json(path)
        if isinstance(payload, dict):
            return payload
    return {
        "name": MARKETPLACE_NAME,
        "interface": {
            "displayName": MARKETPLACE_DISPLAY_NAME,
        },
        "plugins": [],
    }


def _ensure_marketplace_shape(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    name = normalized.get("name")
    if not isinstance(name, str) or not name.strip():
        normalized["name"] = MARKETPLACE_NAME
    interface = normalized.get("interface")
    if not isinstance(interface, dict):
        interface = {}
    display_name = interface.get("displayName")
    if not isinstance(display_name, str) or not display_name.strip():
        interface["displayName"] = MARKETPLACE_DISPLAY_NAME
    normalized["interface"] = interface
    plugins = normalized.get("plugins")
    normalized["plugins"] = list(plugins) if isinstance(plugins, list) else []
    return normalized


def _marketplace_name(path: Path) -> str:
    payload = _ensure_marketplace_shape(_load_marketplace(path))
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        return MARKETPLACE_NAME
    return name.strip()


def _plugin_entry(plugin_name: str, category: str, source_path: str | None = None) -> dict[str, Any]:
    return {
        "name": plugin_name,
        "source": {
            "source": "local",
            "path": source_path or f"./plugins/{plugin_name}",
        },
        "policy": {
            "installation": "AVAILABLE",
            "authentication": "ON_INSTALL",
        },
        "category": category,
    }


def _normalized_plugin_identity(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _source_path_matches_plugin(source_path: object, plugin_name: str) -> bool:
    if not isinstance(source_path, str) or not source_path.strip():
        return False
    normalized_path = source_path.replace("\\", "/").strip().rstrip("/")
    lower_path = normalized_path.lower()
    lower_plugin_name = plugin_name.lower()
    if lower_path in {lower_plugin_name, f"./plugins/{lower_plugin_name}", f"plugins/{lower_plugin_name}"}:
        return True
    if lower_path.endswith(f"/plugins/{lower_plugin_name}"):
        return True
    return _normalized_plugin_identity(Path(normalized_path).name) == _normalized_plugin_identity(plugin_name)


def _marketplace_entry_matches_plugin(entry: object, plugin_name: str) -> bool:
    if not isinstance(entry, dict):
        return False
    plugin_identity = _normalized_plugin_identity(plugin_name)
    if _normalized_plugin_identity(entry.get("name")) == plugin_identity:
        return True
    source = entry.get("source")
    if isinstance(source, dict) and _source_path_matches_plugin(source.get("path"), plugin_name):
        return True
    return False


def _write_marketplace_entry(marketplace_path: Path, plugin_name: str, category: str, source_path: str | None = None) -> int:
    payload = _ensure_marketplace_shape(_load_marketplace(marketplace_path))
    original_plugins = payload["plugins"]
    plugins = [entry for entry in original_plugins if not _marketplace_entry_matches_plugin(entry, plugin_name)]
    removed_count = len(original_plugins) - len(plugins)
    plugins.append(_plugin_entry(plugin_name, category, source_path))
    payload["plugins"] = plugins
    dump_json(marketplace_path, payload)
    return removed_count


def _remove_marketplace_entries(marketplace_path: Path, plugin_name: str) -> int:
    if not marketplace_path.exists():
        return 0
    payload = _ensure_marketplace_shape(_load_marketplace(marketplace_path))
    original_plugins = payload["plugins"]
    plugins = [entry for entry in original_plugins if not _marketplace_entry_matches_plugin(entry, plugin_name)]
    removed_count = len(original_plugins) - len(plugins)
    if removed_count <= 0:
        return 0
    payload["plugins"] = plugins
    dump_json(marketplace_path, payload)
    return removed_count


def _has_preserved_content(path: Path) -> bool:
    if not path.exists():
        return False
    if path.is_dir():
        return any(path.iterdir())
    return True


def _copy_preserved_path(source_path: Path, destination: Path) -> None:
    ensure_dir(destination.parent)
    if source_path.is_dir():
        shutil.copytree(source_path, destination, dirs_exist_ok=True)
    else:
        shutil.copy2(source_path, destination)


def _copy_missing_directory_content(source_path: Path, destination: Path) -> bool:
    copied = False
    for source_child in source_path.rglob("*"):
        relative_path = source_child.relative_to(source_path)
        destination_child = destination / relative_path
        if source_child.is_dir():
            ensure_dir(destination_child)
            continue
        if destination_child.exists():
            continue
        ensure_dir(destination_child.parent)
        shutil.copy2(source_child, destination_child)
        copied = True
    return copied


def _clear_readonly_and_retry(function: Any, path: str, excinfo: tuple[type[BaseException], BaseException, Any]) -> None:
    _ = excinfo
    os.chmod(path, stat.S_IWRITE)
    function(path)


def _copy_plugin_tree(source_root: Path, target_root: Path) -> None:
    ensure_dir(target_root.parent)
    shutil.copytree(
        source_root,
        target_root,
        ignore=shutil.ignore_patterns(*COPY_IGNORE_NAMES),
    )


def _run_command(args: list[str], cwd: Path) -> str:
    completed = subprocess.run(
        args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "Command failed while installing EQEmu Oracle plugin:\n"
            f"command: {' '.join(args)}\n"
            f"cwd: {cwd}\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    return completed.stdout.strip()


def _try_git(args: list[str], cwd: Path) -> str | None:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _source_git_checkout(source_root: Path) -> dict[str, Any] | None:
    repo_root_text = _try_git(["rev-parse", "--show-toplevel"], source_root)
    if not repo_root_text:
        return None
    repo_root = Path(repo_root_text).resolve()
    try:
        plugin_subpath = source_root.resolve().relative_to(repo_root)
    except ValueError:
        return None
    remote_url = _try_git(["config", "--get", "remote.origin.url"], source_root) or str(repo_root)
    branch = _try_git(["rev-parse", "--abbrev-ref", "HEAD"], source_root) or ""
    if branch == "HEAD":
        branch = ""
    return {
        "repo_root": repo_root,
        "plugin_subpath": plugin_subpath,
        "remote_url": remote_url,
        "branch": branch,
    }


def _capture_preserved_paths(target_root: Path, backup_root: Path) -> list[tuple[Path, Path]]:
    captured: list[tuple[Path, Path]] = []
    for relative_path in PRESERVED_PATHS:
        source_path = target_root / relative_path
        if source_path.exists():
            backup_path = backup_root / relative_path
            ensure_dir(backup_path.parent)
            if source_path.is_dir():
                shutil.copytree(source_path, backup_path)
            else:
                shutil.copy2(source_path, backup_path)
            captured.append((relative_path, backup_path))
    return captured


def _restore_preserved_paths(target_root: Path, preserved_paths: list[tuple[Path, Path]]) -> list[str]:
    restored: list[str] = []
    for relative_path, source_path in preserved_paths:
        destination = target_root / relative_path
        ensure_dir(destination.parent)
        if source_path.is_dir():
            shutil.copytree(source_path, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(source_path, destination)
        restored.append(relative_path.as_posix())
    return restored


def _migrate_preserved_paths(source_root: Path, target_root: Path) -> list[str]:
    migrated: list[str] = []
    if not source_root.exists():
        return migrated
    for relative_path in PRESERVED_PATHS:
        source_path = source_root / relative_path
        destination = target_root / relative_path
        if not _has_preserved_content(source_path):
            continue
        if _has_preserved_content(destination):
            if source_path.is_dir() and destination.is_dir() and _copy_missing_directory_content(source_path, destination):
                migrated.append(relative_path.as_posix())
            continue
        _copy_preserved_path(source_path, destination)
        migrated.append(relative_path.as_posix())
    return migrated


def _seed_local_extension_scaffolds(target_root: Path) -> list[str]:
    seeded: list[str] = []
    for relative_path, payload in LOCAL_EXTENSION_SCAFFOLDS.items():
        path = target_root / relative_path
        if path.exists():
            continue
        dump_json(path, payload)
        seeded.append(relative_path.as_posix())
    return seeded


def _validate_target_path(target_root: Path, plugins_root: Path) -> None:
    resolved_target = target_root.resolve()
    resolved_plugins_root = plugins_root.resolve()
    if resolved_target == resolved_plugins_root:
        raise RuntimeError("Refusing to replace the home plugins root itself")
    if resolved_plugins_root not in resolved_target.parents:
        raise RuntimeError(f"Refusing to replace path outside the home plugins root: {resolved_target}")


def _restore_plugin_paths(target_root: Path, preserved_paths: list[tuple[Path, Path]]) -> list[str]:
    if not preserved_paths:
        return []
    return _restore_preserved_paths(target_root, preserved_paths)


def _sync_copied_plugin_contents(source_root: Path, target_root: Path, plugins_root: Path) -> list[str]:
    with tempfile.TemporaryDirectory(prefix="eqemu-oracle-install-") as temp_dir:
        backup_root = Path(temp_dir)
        preserved_paths = _capture_preserved_paths(target_root, backup_root) if target_root.exists() else []
        if target_root.exists():
            _validate_target_path(target_root, plugins_root)
            shutil.rmtree(target_root, onerror=_clear_readonly_and_retry)
        _copy_plugin_tree(source_root, target_root)
        return _restore_plugin_paths(target_root, preserved_paths)


def _clone_git_checkout(git_source: dict[str, Any], checkout_root: Path, plugins_root: Path) -> None:
    if checkout_root.exists():
        _validate_target_path(checkout_root, plugins_root)
        shutil.rmtree(checkout_root, onerror=_clear_readonly_and_retry)
    ensure_dir(checkout_root.parent)
    remote_url = str(git_source["remote_url"])
    branch = str(git_source.get("branch") or "")
    clone_command = ["git", "clone"]
    if branch:
        clone_command.extend(["--branch", branch])
    clone_command.extend([remote_url, str(checkout_root)])
    _run_command(clone_command, plugins_root)


def _sync_git_plugin_contents(source_root: Path, checkout_root: Path, plugins_root: Path, git_source: dict[str, Any]) -> dict[str, Any]:
    plugin_subpath = Path(git_source["plugin_subpath"])
    target_root = checkout_root / plugin_subpath
    if source_root.resolve() == target_root.resolve():
        return {
            "target_root": target_root,
            "checkout_root": checkout_root,
            "restored_paths": [],
            "install_strategy": "existing-git-checkout",
            "git": git_source,
        }

    with tempfile.TemporaryDirectory(prefix="eqemu-oracle-install-") as temp_dir:
        backup_root = Path(temp_dir)
        preserved_paths: list[tuple[Path, Path]] = []
        if checkout_root.exists():
            preserved_paths.extend(_capture_preserved_paths(checkout_root, backup_root / "flat"))
            nested_existing_root = checkout_root / plugin_subpath
            if nested_existing_root != checkout_root:
                preserved_paths.extend(_capture_preserved_paths(nested_existing_root, backup_root / "nested"))
        _clone_git_checkout(git_source, checkout_root, plugins_root)
        if not target_root.exists():
            raise RuntimeError(f"Cloned repository does not contain plugin subpath: {plugin_subpath.as_posix()}")
        restored_paths = _restore_plugin_paths(target_root, preserved_paths)
    return {
        "target_root": target_root,
        "checkout_root": checkout_root,
        "restored_paths": restored_paths,
        "install_strategy": "git-checkout",
        "git": git_source,
    }


def _sync_plugin_contents(source_root: Path, plugins_root: Path, plugin_name: str) -> dict[str, Any]:
    git_source = _source_git_checkout(source_root)
    if git_source is not None:
        checkout_root = plugins_root / plugin_name
        return _sync_git_plugin_contents(source_root, checkout_root, plugins_root, git_source)

    target_root = plugins_root / plugin_name
    if source_root.resolve() == target_root.resolve():
        restored_paths: list[str] = []
    else:
        restored_paths = _sync_copied_plugin_contents(source_root, target_root, plugins_root)
    return {
        "target_root": target_root,
        "checkout_root": target_root,
        "restored_paths": restored_paths,
        "install_strategy": "copy",
        "git": None,
    }


def _has_active_local_extensions(target_root: Path) -> bool:
    local_root = target_root / "local-extensions"
    return any(load_domain_extensions(local_root, domain) for domain in DOMAIN_CHOICES)


def _rebuild_target_plugin(target_root: Path, *, git_checkout: bool = False) -> dict[str, Any]:
    has_active_local_extensions = _has_active_local_extensions(target_root)
    if git_checkout and not has_active_local_extensions:
        return {
            "ran": False,
            "reason": "git checkout install uses committed merged data from the repository",
        }
    script_path = target_root / "scripts" / "eqemu_oracle.py"
    mode = "overlay" if has_active_local_extensions else "committed"
    completed = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "rebuild-extensions",
            "--scope",
            "all",
            "--mode",
            mode,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "Installed plugin rebuild failed:\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    return {
        "command": [sys.executable, str(script_path), "rebuild-extensions", "--scope", "all", "--mode", mode],
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def _legacy_marketplace_path(home: Path) -> Path:
    return home / ".agents" / "plugins" / "marketplace.json"


def _legacy_plugins_root(home: Path) -> Path:
    return home / "plugins"


def _codex_root(home: Path) -> Path:
    return home / ".codex"


def _codex_marketplace_root(home: Path) -> Path:
    return _codex_root(home) / "local-marketplaces" / MARKETPLACE_NAME


def _codex_marketplace_path(home: Path) -> Path:
    return _codex_marketplace_root(home) / ".agents" / "plugins" / "marketplace.json"


def _codex_plugins_root(home: Path) -> Path:
    return _codex_marketplace_root(home) / "plugins"


def _codex_plugin_cache_root(home: Path) -> Path:
    return _codex_root(home) / "plugins" / "cache"


def _codex_cache_activation_root(home: Path, marketplace_name: str, plugin_name: str) -> Path:
    return _codex_plugin_cache_root(home) / marketplace_name / plugin_name / "local"


def _resolve_install_target(home: Path) -> dict[str, Path | str]:
    if _codex_root(home).exists():
        return {
            "install_kind": CODEX_DESKTOP_INSTALL_KIND,
            "marketplace_path": _codex_marketplace_path(home),
            "plugins_root": _codex_plugins_root(home),
        }
    return {
        "install_kind": LEGACY_HOME_INSTALL_KIND,
        "marketplace_path": _legacy_marketplace_path(home),
        "plugins_root": _legacy_plugins_root(home),
    }


def _same_resolved_path(left: Path, right: Path) -> bool:
    return left.resolve() == right.resolve()


def _marketplace_root(marketplace_path: Path) -> Path:
    return marketplace_path.parents[2]


def _marketplace_source_path(marketplace_path: Path, target_root: Path) -> str:
    root = _marketplace_root(marketplace_path).resolve()
    try:
        relative_path = target_root.resolve().relative_to(root)
    except ValueError:
        return str(target_root.resolve())
    return f"./{relative_path.as_posix()}"


def _known_marketplace_paths(home: Path) -> tuple[Path, ...]:
    official_codex_marketplace_path = _codex_root(home) / ".tmp" / "plugins" / ".agents" / "plugins" / "marketplace.json"
    return (_codex_marketplace_path(home), official_codex_marketplace_path, _legacy_marketplace_path(home))


def _prune_inactive_marketplace_entries(home: Path, active_marketplace_path: Path, plugin_name: str) -> list[dict[str, Any]]:
    pruned: list[dict[str, Any]] = []
    for marketplace_path in _known_marketplace_paths(home):
        if _same_resolved_path(marketplace_path, active_marketplace_path):
            continue
        removed_count = _remove_marketplace_entries(marketplace_path, plugin_name)
        if removed_count > 0:
            pruned.append(
                {
                    "marketplace_path": str(marketplace_path.resolve()),
                    "removed_entries": removed_count,
                }
            )
    return pruned


def _stale_codex_cache_plugin_roots(home: Path, plugin_name: str, target_root: Path) -> list[Path]:
    cache_root = _codex_plugin_cache_root(home)
    if not cache_root.exists():
        return []
    plugin_identity = _normalized_plugin_identity(plugin_name)
    stale_roots: list[Path] = []
    for candidate in cache_root.glob("*/*/*"):
        if not candidate.is_dir():
            continue
        if (
            _normalized_plugin_identity(candidate.parent.name) == plugin_identity
            and not _same_resolved_path(candidate, target_root)
        ):
            stale_roots.append(candidate)
    return stale_roots


def _validate_stale_cache_plugin_root(cache_plugin_root: Path, home: Path, plugin_name: str) -> None:
    resolved_root = cache_plugin_root.resolve()
    resolved_cache_root = _codex_plugin_cache_root(home).resolve()
    if resolved_cache_root not in resolved_root.parents:
        raise RuntimeError(f"Refusing to remove plugin cache path outside Codex cache root: {resolved_root}")
    plugin_identity = _normalized_plugin_identity(plugin_name)
    if _normalized_plugin_identity(cache_plugin_root.parent.name) != plugin_identity:
        raise RuntimeError(f"Refusing to remove unexpected plugin cache path: {resolved_root}")


def _remove_directory_link_or_tree(path: Path) -> None:
    is_junction = getattr(path, "is_junction", None)
    if path.is_symlink() or (callable(is_junction) and is_junction()):
        path.rmdir()
    else:
        shutil.rmtree(path, onerror=_clear_readonly_and_retry)


def _prune_stale_codex_cache_installs(home: Path, plugin_name: str, target_root: Path) -> list[dict[str, Any]]:
    pruned: list[dict[str, Any]] = []
    for cache_plugin_root in _stale_codex_cache_plugin_roots(home, plugin_name, target_root):
        _validate_stale_cache_plugin_root(cache_plugin_root, home, plugin_name)
        migrated_paths = _migrate_preserved_paths(cache_plugin_root, target_root)
        _remove_directory_link_or_tree(cache_plugin_root)
        plugin_cache_root = cache_plugin_root.parent
        if plugin_cache_root.exists() and not any(plugin_cache_root.iterdir()):
            plugin_cache_root.rmdir()
        pruned.append(
            {
                "plugin_root": str(cache_plugin_root.resolve()),
                "migrated_paths": migrated_paths,
            }
        )
    return pruned


def _sync_codex_cache_activation_copy(home: Path, marketplace_name: str, plugin_name: str, target_root: Path) -> dict[str, Any]:
    cache_plugin_root = _codex_cache_activation_root(home, marketplace_name, plugin_name)
    migrated_paths: list[str] = []
    if cache_plugin_root.exists():
        _validate_stale_cache_plugin_root(cache_plugin_root, home, plugin_name)
        if not _same_resolved_path(cache_plugin_root, target_root):
            migrated_paths = _migrate_preserved_paths(cache_plugin_root, target_root)
        _remove_directory_link_or_tree(cache_plugin_root)
    _copy_plugin_tree(target_root, cache_plugin_root)
    return {
        "plugin_root": str(cache_plugin_root.resolve()),
        "target_root": str(target_root.resolve()),
        "synced": True,
        "migrated_paths": migrated_paths,
    }


def _plugin_config_header(plugin_name: str, marketplace_name: str) -> str:
    return f'[plugins."{plugin_name}@{marketplace_name}"]'


def _mcp_server_config_header(server_name: str) -> str:
    return f'[mcp_servers."{server_name}"]'


def _marketplace_config_header(marketplace_name: str) -> str:
    return f'[marketplaces."{marketplace_name}"]'


_TOML_TABLE_HEADER_RE = re.compile(r"(?m)^[ \t]*\[{1,2}[^\n]+?\]{1,2}[ \t]*(?:#.*)?$")
_CODEX_PLUGIN_HEADER_RE = re.compile(
    r"""^[ \t]*\[plugins\.(?P<quote>["'])(?P<plugin>[^"'@\n]+)@(?P<marketplace>[^"'\n]+)(?P=quote)\][ \t]*(?:#.*)?$"""
)
_CODEX_MCP_SERVER_HEADER_RE = re.compile(
    r"""^[ \t]*\[mcp_servers\.(?:(?P<quote>["'])(?P<quoted>[^"'\n]+)(?P=quote)|(?P<bare>[A-Za-z0-9_-]+))\][ \t]*(?:#.*)?$"""
)
_CODEX_MARKETPLACE_HEADER_RE = re.compile(
    r"""^[ \t]*\[marketplaces\.(?:(?P<quote>["'])(?P<quoted>[^"'\n]+)(?P=quote)|(?P<bare>[A-Za-z0-9_-]+))\][ \t]*(?:#.*)?$"""
)


def _codex_plugin_header_info(header_line: str) -> tuple[str, str] | None:
    match = _CODEX_PLUGIN_HEADER_RE.fullmatch(header_line.strip())
    if match is None:
        return None
    return (match.group("plugin"), match.group("marketplace"))


def _codex_mcp_server_header_info(header_line: str) -> str | None:
    match = _CODEX_MCP_SERVER_HEADER_RE.fullmatch(header_line.strip())
    if match is None:
        return None
    return match.group("quoted") or match.group("bare")


def _codex_marketplace_header_info(header_line: str) -> str | None:
    match = _CODEX_MARKETPLACE_HEADER_RE.fullmatch(header_line.strip())
    if match is None:
        return None
    return match.group("quoted") or match.group("bare")


def _toml_basic_string(value: str) -> str:
    return json.dumps(value)


def _toml_string_array(values: list[str]) -> str:
    return "[" + ", ".join(_toml_basic_string(value) for value in values) + "]"


def _replace_section_header(section: str, header: str) -> str:
    lines = section.splitlines(keepends=True)
    if not lines:
        return f"{header}\n"
    first_line = lines[0]
    line_ending = "\n" if first_line.endswith("\n") else ""
    lines[0] = f"{header}{line_ending}"
    return "".join(lines)


def _find_unquoted_char(value: str, target: str) -> int:
    in_single = False
    in_double = False
    escaped = False
    for index, char in enumerate(value):
        if char == "\\" and in_double and not escaped:
            escaped = True
            continue
        if char == "'" and not in_double and not escaped:
            in_single = not in_single
        elif char == '"' and not in_single and not escaped:
            in_double = not in_double
        elif char == target and not in_single and not in_double:
            return index
        escaped = False
    return -1


def _split_toml_key(raw_key: str) -> list[str]:
    parts: list[str] = []
    remainder = raw_key.strip()
    while remainder:
        if remainder[0] in {'"', "'"}:
            quote = remainder[0]
            escaped = False
            end_index = -1
            for index, char in enumerate(remainder[1:], start=1):
                if char == "\\" and quote == '"' and not escaped:
                    escaped = True
                    continue
                if char == quote and not escaped:
                    end_index = index
                    break
                escaped = False
            if end_index < 0:
                return []
            parts.append(remainder[1:end_index])
            remainder = remainder[end_index + 1 :].strip()
        else:
            dot_index = _find_unquoted_char(remainder, ".")
            if dot_index < 0:
                part = remainder.strip()
                remainder = ""
            else:
                part = remainder[:dot_index].strip()
                remainder = remainder[dot_index:].strip()
            if not part:
                return []
            parts.append(part)

        if not remainder:
            break
        if not remainder.startswith("."):
            return []
        remainder = remainder[1:].strip()
    return parts


def _toml_assignment_key(line: str) -> str | None:
    stripped = line.lstrip()
    if not stripped or stripped.startswith(("#", "[")):
        return None
    equals_index = _find_unquoted_char(stripped, "=")
    if equals_index < 0:
        return None
    key_parts = _split_toml_key(stripped[:equals_index])
    if len(key_parts) != 1:
        return None
    return key_parts[0]


def _is_enabled_assignment(line: str) -> bool:
    return _toml_assignment_key(line) == "enabled"


def _ensure_section_enabled(section: str) -> str:
    lines = section.splitlines(keepends=True)
    if not lines:
        return "enabled = true\n"
    if not lines[0].endswith(("\n", "\r")):
        lines[0] = f"{lines[0]}\n"

    normalized_lines = [lines[0]]
    wrote_enabled = False
    for line in lines[1:]:
        if _is_enabled_assignment(line):
            if not wrote_enabled:
                normalized_lines.append("enabled = true\n")
                wrote_enabled = True
            continue
        normalized_lines.append(line)
    if not wrote_enabled:
        if len(normalized_lines) > 1 and not normalized_lines[-1].endswith(("\n", "\r")):
            normalized_lines[-1] = f"{normalized_lines[-1]}\n"
        normalized_lines.append("enabled = true\n")
    return "".join(normalized_lines)


def _append_codex_plugin_section(text: str, header: str) -> str:
    suffix = ""
    if text and not text.endswith("\n"):
        suffix += "\n"
    if text and not text.endswith("\n\n"):
        suffix += "\n"
    return f"{text}{suffix}{header}\nenabled = true\n"


def _append_toml_section(text: str, section: str) -> str:
    suffix = ""
    if text and not text.endswith("\n"):
        suffix += "\n"
    if text and not text.endswith("\n\n"):
        suffix += "\n"
    return f"{text}{suffix}{section}"


def _validate_codex_config_toml(text: str, config_path: Path) -> None:
    if _tomllib is None:
        return
    try:
        _tomllib.loads(text)
    except Exception as exc:
        raise CodexConfigError(f"Refusing to write invalid Codex config TOML at {config_path}: {exc}") from exc


def _write_codex_config_atomically(config_path: Path, text: str) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(config_path.parent),
        prefix=f".{config_path.name}.",
        suffix=".tmp",
        delete=False,
    ) as temp_file:
        temp_file.write(text)
        temp_path = Path(temp_file.name)
    try:
        os.replace(temp_path, config_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _normalize_codex_plugin_config(text: str, plugin_name: str, marketplace_name: str) -> str:
    header = _plugin_config_header(plugin_name, marketplace_name)
    table_matches = list(_TOML_TABLE_HEADER_RE.finditer(text))
    if not table_matches:
        return _append_codex_plugin_section(text, header)

    plugin_sections: list[tuple[int, str]] = []
    section_infos: list[tuple[int, int, tuple[str, str] | None]] = []
    for index, match in enumerate(table_matches):
        section_start = match.start()
        section_end = table_matches[index + 1].start() if index + 1 < len(table_matches) else len(text)
        info = _codex_plugin_header_info(match.group(0))
        section_infos.append((section_start, section_end, info))
        if info is not None and info[0] == plugin_name:
            plugin_sections.append((index, info[1]))

    if not plugin_sections:
        return _append_codex_plugin_section(text, header)

    keep_index = next((index for index, marketplace in plugin_sections if marketplace == marketplace_name), plugin_sections[0][0])
    pieces = [text[: table_matches[0].start()]]
    for index, (section_start, section_end, info) in enumerate(section_infos):
        section = text[section_start:section_end]
        if info is not None and info[0] == plugin_name:
            if index != keep_index:
                continue
            section = _replace_section_header(section, header)
            section = _ensure_section_enabled(section)
        pieces.append(section)
    return "".join(pieces)


def _mcp_server_section(server_name: str, target_root: Path) -> str:
    script_path = target_root / "scripts" / "eqemu_oracle.py"
    return "\n".join(
        [
            _mcp_server_config_header(server_name),
            f"command = {_toml_basic_string(str(Path(sys.executable).resolve()))}",
            f"args = {_toml_string_array([str(script_path.resolve()), 'mcp-serve'])}",
            f"cwd = {_toml_basic_string(str(target_root.resolve()))}",
            "",
        ]
    )


def _marketplace_source_section(marketplace_name: str, marketplace_root: Path) -> str:
    return "\n".join(
        [
            _marketplace_config_header(marketplace_name),
            'source_type = "local"',
            f"source = {_toml_basic_string(str(marketplace_root.resolve()))}",
            "",
        ]
    )


def _normalize_codex_mcp_server_config(text: str, server_name: str, target_root: Path, legacy_server_names: tuple[str, ...] = ()) -> str:
    section_text = _mcp_server_section(server_name, target_root)
    server_names = {server_name, *legacy_server_names}
    table_matches = list(_TOML_TABLE_HEADER_RE.finditer(text))
    if not table_matches:
        return _append_toml_section(text, section_text)

    server_sections: list[int] = []
    section_infos: list[tuple[int, int, str | None]] = []
    for index, match in enumerate(table_matches):
        section_start = match.start()
        section_end = table_matches[index + 1].start() if index + 1 < len(table_matches) else len(text)
        info = _codex_mcp_server_header_info(match.group(0))
        section_infos.append((section_start, section_end, info))
        if info in server_names:
            server_sections.append(index)

    if not server_sections:
        return _append_toml_section(text, section_text)

    keep_index = server_sections[0]
    pieces = [text[: table_matches[0].start()]]
    for index, (section_start, section_end, info) in enumerate(section_infos):
        section = text[section_start:section_end]
        if info in server_names:
            if index != keep_index:
                continue
            section = section_text
        pieces.append(section)
    return "".join(pieces)


def _normalize_codex_marketplace_source_config(text: str, marketplace_name: str, marketplace_root: Path) -> str:
    section_text = _marketplace_source_section(marketplace_name, marketplace_root)
    table_matches = list(_TOML_TABLE_HEADER_RE.finditer(text))
    if not table_matches:
        return _append_toml_section(text, section_text)

    marketplace_sections: list[int] = []
    section_infos: list[tuple[int, int, str | None]] = []
    for index, match in enumerate(table_matches):
        section_start = match.start()
        section_end = table_matches[index + 1].start() if index + 1 < len(table_matches) else len(text)
        info = _codex_marketplace_header_info(match.group(0))
        section_infos.append((section_start, section_end, info))
        if info == marketplace_name:
            marketplace_sections.append(index)

    if not marketplace_sections:
        return _append_toml_section(text, section_text)

    keep_index = marketplace_sections[0]
    pieces = [text[: table_matches[0].start()]]
    for index, (section_start, section_end, info) in enumerate(section_infos):
        section = text[section_start:section_end]
        if info == marketplace_name:
            if index != keep_index:
                continue
            section = section_text
        pieces.append(section)
    return "".join(pieces)


def validate_codex_config(home: Path | None = None) -> str | None:
    resolved_home = home.resolve() if home is not None else Path.home().resolve()
    codex_root = _codex_root(resolved_home)
    if not codex_root.exists():
        return None
    config_path = codex_root / "config.toml"
    if not config_path.exists():
        return None
    _validate_codex_config_toml(config_path.read_text(encoding="utf-8"), config_path)
    return str(config_path.resolve())


def _enable_codex_plugin(
    home: Path,
    plugin_name: str,
    marketplace_name: str,
    target_root: Path | None = None,
    marketplace_root: Path | None = None,
) -> str | None:
    codex_root = _codex_root(home)
    if not codex_root.exists():
        return None
    config_path = codex_root / "config.toml"
    if config_path.exists():
        text = config_path.read_text(encoding="utf-8")
    else:
        text = ""
    if marketplace_root is not None:
        text = _normalize_codex_marketplace_source_config(text, marketplace_name, marketplace_root)
    text = _normalize_codex_plugin_config(text, plugin_name, marketplace_name)
    if target_root is not None:
        text = _normalize_codex_mcp_server_config(text, CODEX_MCP_SERVER_NAME, target_root, LEGACY_CODEX_MCP_SERVER_NAMES)
    _validate_codex_config_toml(text, config_path)
    _write_codex_config_atomically(config_path, text)
    return str(config_path.resolve())


def install_global_plugin(
    *,
    home: Path | None = None,
    source_plugin_root: Path = PLUGIN_ROOT,
) -> dict[str, Any]:
    resolved_home = home.resolve() if home is not None else Path.home().resolve()
    install_target = _resolve_install_target(resolved_home)
    marketplace_path = Path(install_target["marketplace_path"])
    plugins_root = Path(install_target["plugins_root"])
    install_kind = str(install_target["install_kind"])
    plugin_name = _plugin_name_for_source(source_plugin_root)
    sync_result = _sync_plugin_contents(source_plugin_root, plugins_root, plugin_name)
    target_root = Path(sync_result["target_root"])
    checkout_root = Path(sync_result["checkout_root"])
    restored_paths = list(sync_result["restored_paths"])
    migrated_paths: list[str] = []
    legacy_target_root = _legacy_plugins_root(resolved_home) / plugin_name
    if install_kind == CODEX_DESKTOP_INSTALL_KIND and legacy_target_root.resolve() != target_root.resolve():
        migrated_paths = _migrate_preserved_paths(legacy_target_root, target_root)
    marketplace_name = _marketplace_name(marketplace_path)
    pruned_stale_cache_installs = _prune_stale_codex_cache_installs(resolved_home, plugin_name, target_root)
    codex_cache_activation_copy: dict[str, Any] | None = None
    if install_kind == CODEX_DESKTOP_INSTALL_KIND:
        codex_cache_activation_copy = _sync_codex_cache_activation_copy(resolved_home, marketplace_name, plugin_name, target_root)
    seeded_local_extension_files = _seed_local_extension_scaffolds(target_root)
    category = _category_for_plugin(source_plugin_root)
    marketplace_source_path = _marketplace_source_path(marketplace_path, target_root)
    replaced_active_marketplace_entries = _write_marketplace_entry(marketplace_path, plugin_name, category, marketplace_source_path)
    pruned_inactive_marketplace_entries = _prune_inactive_marketplace_entries(resolved_home, marketplace_path, plugin_name)
    rebuild = _rebuild_target_plugin(target_root, git_checkout=sync_result["git"] is not None)
    codex_config_path: str | None = None
    if _codex_root(resolved_home).exists():
        codex_config_path = _enable_codex_plugin(
            resolved_home,
            plugin_name,
            marketplace_name,
            target_root,
            _marketplace_root(marketplace_path),
        )
    return {
        "install_kind": install_kind,
        "codex_cache_plugin_root": codex_cache_activation_copy["plugin_root"] if codex_cache_activation_copy is not None else None,
        "codex_cache_activation_copy": codex_cache_activation_copy,
        "codex_config_path": codex_config_path,
        "plugin_name": plugin_name,
        "source_plugin_root": str(source_plugin_root.resolve()),
        "target_plugin_root": str(target_root.resolve()),
        "checkout_root": str(checkout_root.resolve()),
        "install_strategy": sync_result["install_strategy"],
        "git": (
            {
                "repo_root": str(Path(sync_result["git"]["repo_root"]).resolve()),
                "plugin_subpath": Path(sync_result["git"]["plugin_subpath"]).as_posix(),
                "remote_url": sync_result["git"]["remote_url"],
                "branch": sync_result["git"]["branch"],
            }
            if sync_result["git"] is not None
            else None
        ),
        "marketplace_path": str(marketplace_path.resolve()),
        "marketplace_source_path": marketplace_source_path,
        "replaced_active_marketplace_entries": replaced_active_marketplace_entries,
        "pruned_inactive_marketplace_entries": pruned_inactive_marketplace_entries,
        "pruned_stale_cache_installs": pruned_stale_cache_installs,
        "restored_paths": restored_paths,
        "migrated_paths": migrated_paths,
        "seeded_local_extension_files": seeded_local_extension_files,
        "rebuild": rebuild,
    }
