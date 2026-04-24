from __future__ import annotations

import os
import shutil
import stat
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .constants import BASE_ROOT, CACHE_ROOT, DOMAIN_CHOICES, LOCAL_EXTENSIONS_ROOT, MAINTENANCE_LOCK_ROOT, MERGED_ROOT, MODE_CHOICES, OVERLAY_ROOT, REPO_ROOT, SCOPE_CHOICES
from .dataset import prune_stale_schema_extensions, write_merged_dataset
from .extensions import load_domain_extensions
from .ingest import write_base_dataset


def _remove_tree(path: Path) -> None:
    if not path.exists():
        return

    def onerror(func, failed_path, exc_info):  # type: ignore[no-untyped-def]
        failed = Path(failed_path)
        mode = failed.stat().st_mode if failed.exists() else 0
        writable_mode = mode | stat.S_IWUSR
        if failed.is_dir():
            writable_mode |= stat.S_IXUSR
        os.chmod(failed_path, writable_mode or stat.S_IWRITE)
        func(failed_path)

    shutil.rmtree(path, onerror=onerror)


def _remove_domain_trees(base_root: Path, merged_root: Path, scope: str) -> None:
    if scope == "all":
        for path in (base_root, merged_root):
            if path.exists():
                _remove_tree(path)
        return
    for path in (base_root / scope, merged_root / scope):
        if path.exists():
            _remove_tree(path)


@contextmanager
def maintenance_lock(timeout_seconds: float = 30.0):
    ensure_root = CACHE_ROOT
    ensure_root.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            MAINTENANCE_LOCK_ROOT.mkdir()
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise RuntimeError("Timed out waiting for another EQEmu Oracle maintenance operation to finish.")
            time.sleep(0.1)
    try:
        yield
    finally:
        if MAINTENANCE_LOCK_ROOT.exists():
            _remove_tree(MAINTENANCE_LOCK_ROOT)


def _roots_for_mode(mode: str) -> tuple[Path, Path]:
    if mode not in MODE_CHOICES:
        raise ValueError(f"Unsupported mode '{mode}'. Expected one of: {', '.join(MODE_CHOICES)}")
    if mode == "committed":
        return BASE_ROOT, MERGED_ROOT
    return OVERLAY_ROOT / "base", OVERLAY_ROOT / "merged"


def _ensure_overlay_base(target_base: Path) -> None:
    if target_base.exists():
        return
    ensure_parent = target_base.parent
    ensure_parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(BASE_ROOT, target_base)


def _path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _active_local_extension_files() -> list[str]:
    files: set[str] = set()
    for domain in DOMAIN_CHOICES:
        for entry in load_domain_extensions(LOCAL_EXTENSIONS_ROOT, domain):
            extension_file = entry.get("_extension_file")
            if isinstance(extension_file, str) and extension_file:
                files.add(extension_file)
    return sorted(files)


def _assert_committed_rebuild_is_safe(target_merged: Path) -> None:
    if not _path_is_relative_to(target_merged, REPO_ROOT):
        return
    if not (REPO_ROOT / ".git").exists():
        return
    active_files = _active_local_extension_files()
    if not active_files:
        return
    file_list = "\n- ".join(active_files)
    raise RuntimeError(
        "Refusing to write committed merged data with active local extensions in a git checkout.\n"
        "Local extensions are private and should be rebuilt into the ignored overlay cache instead:\n"
        "  python3 plugins/eqemu-oracle/scripts/eqemu_oracle.py rebuild-extensions --scope all --mode overlay\n"
        f"Active local extension files:\n- {file_list}"
    )


def refresh_dataset(*, scope: str, mode: str) -> dict[str, Any]:
    if scope not in SCOPE_CHOICES:
        raise ValueError(f"Unsupported scope '{scope}'. Expected one of: {', '.join(SCOPE_CHOICES)}")
    with maintenance_lock():
        target_base, target_merged = _roots_for_mode(mode)
        if mode == "committed":
            _assert_committed_rebuild_is_safe(target_merged)
        _remove_domain_trees(target_base, target_merged, scope)
        if mode == "committed" and scope == "all" and OVERLAY_ROOT.exists():
            _remove_tree(OVERLAY_ROOT)
        write_base_dataset(target_base, scope=scope)
        return write_merged_dataset(target_base, target_merged, scope=scope)


def rebuild_extensions_dataset(*, scope: str, mode: str) -> dict[str, Any]:
    if scope not in SCOPE_CHOICES:
        raise ValueError(f"Unsupported scope '{scope}'. Expected one of: {', '.join(SCOPE_CHOICES)}")
    with maintenance_lock():
        target_base, target_merged = _roots_for_mode(mode)
        if mode == "committed":
            _assert_committed_rebuild_is_safe(target_merged)
        if mode == "overlay":
            _ensure_overlay_base(target_base)
        return write_merged_dataset(target_base, target_merged, scope=scope)


def prune_schema_extensions_dataset(*, apply: bool, mode: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    with maintenance_lock():
        target_base, target_merged = _roots_for_mode(mode)
        result = prune_stale_schema_extensions(target_base, apply=apply)
        manifest = None
        if apply and result.get("removed_count"):
            manifest = write_merged_dataset(target_base, target_merged, scope="schema")
        return result, manifest
