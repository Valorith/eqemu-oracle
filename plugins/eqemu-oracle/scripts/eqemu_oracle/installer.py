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

from .constants import PLUGIN_ROOT
from .utils import dump_json, ensure_dir, load_json


MARKETPLACE_NAME = "user-local"
MARKETPLACE_DISPLAY_NAME = "Local Plugins"
CODEX_DESKTOP_INSTALL_KIND = "codex-desktop-marketplace"
LEGACY_HOME_INSTALL_KIND = "legacy-home-marketplace"
PRESERVED_PATHS = (
    Path("config") / "sources.local.toml",
    Path("local-extensions"),
)
COPY_IGNORE_NAMES = {
    "__pycache__",
    ".pytest_cache",
    "tests",
}


def _category_for_plugin(plugin_root: Path) -> str:
    metadata_path = plugin_root / ".codex-plugin" / "plugin.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
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


def _plugin_entry(plugin_name: str, category: str) -> dict[str, Any]:
    return {
        "name": plugin_name,
        "source": {
            "source": "local",
            "path": f"./plugins/{plugin_name}",
        },
        "policy": {
            "installation": "AVAILABLE",
            "authentication": "ON_INSTALL",
        },
        "category": category,
    }


def _write_marketplace_entry(marketplace_path: Path, plugin_name: str, category: str) -> None:
    payload = _ensure_marketplace_shape(_load_marketplace(marketplace_path))
    plugins = [entry for entry in payload["plugins"] if not (isinstance(entry, dict) and entry.get("name") == plugin_name)]
    plugins.append(_plugin_entry(plugin_name, category))
    payload["plugins"] = plugins
    dump_json(marketplace_path, payload)


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
            continue
        _copy_preserved_path(source_path, destination)
        migrated.append(relative_path.as_posix())
    return migrated


def _validate_target_path(target_root: Path, plugins_root: Path) -> None:
    resolved_target = target_root.resolve()
    resolved_plugins_root = plugins_root.resolve()
    if resolved_target == resolved_plugins_root:
        raise RuntimeError("Refusing to replace the home plugins root itself")
    if resolved_plugins_root not in resolved_target.parents:
        raise RuntimeError(f"Refusing to replace path outside the home plugins root: {resolved_target}")


def _sync_plugin_contents(source_root: Path, target_root: Path, plugins_root: Path) -> list[str]:
    if source_root.resolve() == target_root.resolve():
        return []
    with tempfile.TemporaryDirectory(prefix="eqemu-oracle-install-") as temp_dir:
        backup_root = Path(temp_dir)
        preserved_paths = _capture_preserved_paths(target_root, backup_root) if target_root.exists() else []
        if target_root.exists():
            _validate_target_path(target_root, plugins_root)
            shutil.rmtree(target_root, onexc=_clear_readonly_and_retry)
        _copy_plugin_tree(source_root, target_root)
        return _restore_preserved_paths(target_root, preserved_paths)


def _rebuild_target_plugin(target_root: Path) -> dict[str, Any]:
    script_path = target_root / "scripts" / "eqemu_oracle.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "rebuild-extensions",
            "--scope",
            "all",
            "--mode",
            "committed",
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
        "command": [sys.executable, str(script_path), "rebuild-extensions", "--scope", "all", "--mode", "committed"],
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
    return _codex_root(home) / ".tmp" / "plugins"


def _codex_marketplace_path(home: Path) -> Path:
    return _codex_marketplace_root(home) / ".agents" / "plugins" / "marketplace.json"


def _codex_plugins_root(home: Path) -> Path:
    return _codex_marketplace_root(home) / "plugins"


def _resolve_install_target(home: Path) -> dict[str, Path | str]:
    codex_marketplace_path = _codex_marketplace_path(home)
    codex_plugins_root = _codex_plugins_root(home)
    if codex_marketplace_path.exists() and codex_plugins_root.exists():
        return {
            "install_kind": CODEX_DESKTOP_INSTALL_KIND,
            "marketplace_path": codex_marketplace_path,
            "plugins_root": codex_plugins_root,
        }
    return {
        "install_kind": LEGACY_HOME_INSTALL_KIND,
        "marketplace_path": _legacy_marketplace_path(home),
        "plugins_root": _legacy_plugins_root(home),
    }


def _codex_cache_plugin_root(home: Path, marketplace_name: str, plugin_name: str) -> Path:
    return _codex_root(home) / "plugins" / "cache" / marketplace_name / plugin_name / "local"


def _plugin_config_header(plugin_name: str, marketplace_name: str) -> str:
    return f'[plugins."{plugin_name}@{marketplace_name}"]'


def _enable_codex_plugin(home: Path, plugin_name: str, marketplace_name: str) -> str | None:
    codex_root = _codex_root(home)
    if not codex_root.exists():
        return None
    config_path = codex_root / "config.toml"
    header = _plugin_config_header(plugin_name, marketplace_name)
    if config_path.exists():
        text = config_path.read_text(encoding="utf-8")
    else:
        text = ""
    section_pattern = re.compile(
        rf"(?ms)^{re.escape(header)}\n(?P<body>(?:^(?!\[).*\n?)*)"
    )
    match = section_pattern.search(text)
    if match is None:
        suffix = ""
        if text and not text.endswith("\n"):
            suffix += "\n"
        if text and not text.endswith("\n\n"):
            suffix += "\n"
        text += f"{suffix}{header}\nenabled = true\n"
    else:
        body = match.group("body")
        if re.search(r"(?m)^enabled\s*=\s*true\s*$", body):
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(text, encoding="utf-8")
            return str(config_path.resolve())
        if re.search(r"(?m)^enabled\s*=\s*false\s*$", body):
            updated_body = re.sub(r"(?m)^enabled\s*=\s*false\s*$", "enabled = true", body, count=1)
        else:
            separator = "" if not body or body.endswith("\n") else "\n"
            updated_body = f"{body}{separator}enabled = true\n"
        text = f"{text[:match.start('body')]}{updated_body}{text[match.end('body'):]}"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(text, encoding="utf-8")
    return str(config_path.resolve())


def install_home_local_plugin(
    *,
    home: Path | None = None,
    source_plugin_root: Path = PLUGIN_ROOT,
) -> dict[str, Any]:
    resolved_home = home.resolve() if home is not None else Path.home().resolve()
    install_target = _resolve_install_target(resolved_home)
    marketplace_path = Path(install_target["marketplace_path"])
    plugins_root = Path(install_target["plugins_root"])
    install_kind = str(install_target["install_kind"])
    plugin_name = source_plugin_root.name
    target_root = plugins_root / plugin_name
    restored_paths = _sync_plugin_contents(source_plugin_root, target_root, plugins_root)
    migrated_paths: list[str] = []
    legacy_target_root = _legacy_plugins_root(resolved_home) / plugin_name
    if install_kind == CODEX_DESKTOP_INSTALL_KIND and legacy_target_root.resolve() != target_root.resolve():
        migrated_paths = _migrate_preserved_paths(legacy_target_root, target_root)
    category = _category_for_plugin(source_plugin_root)
    _write_marketplace_entry(marketplace_path, plugin_name, category)
    rebuild = _rebuild_target_plugin(target_root)
    marketplace_name = _marketplace_name(marketplace_path)
    codex_cache_root: str | None = None
    codex_config_path: str | None = None
    if _codex_root(resolved_home).exists():
        cache_target_root = _codex_cache_plugin_root(resolved_home, marketplace_name, plugin_name)
        cache_plugins_root = _codex_root(resolved_home) / "plugins" / "cache"
        _sync_plugin_contents(target_root, cache_target_root, cache_plugins_root)
        codex_cache_root = str(cache_target_root.resolve())
        codex_config_path = _enable_codex_plugin(resolved_home, plugin_name, marketplace_name)
    return {
        "install_kind": install_kind,
        "codex_cache_plugin_root": codex_cache_root,
        "codex_config_path": codex_config_path,
        "plugin_name": plugin_name,
        "source_plugin_root": str(source_plugin_root.resolve()),
        "target_plugin_root": str(target_root.resolve()),
        "marketplace_path": str(marketplace_path.resolve()),
        "restored_paths": restored_paths,
        "migrated_paths": migrated_paths,
        "rebuild": rebuild,
    }
