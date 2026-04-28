from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from .constants import PLUGIN_METADATA_PATH, REPO_ROOT


SKIPPED_ROOTS = {".git", "dist", "dist-local-smoke"}
SKIPPED_NAMES = {".DS_Store", "__pycache__", ".pytest_cache"}
SKIPPED_SUFFIXES = {".pyc", ".pyo"}
EXECUTABLE_BUNDLE_NAMES = {"install.sh", "install.command", "eqemu_oracle_launcher.cmd"}


def _should_skip_bundle_path(rel_path: Path) -> bool:
    if rel_path.parts and rel_path.parts[0] in SKIPPED_ROOTS:
        return True
    if any(part in SKIPPED_NAMES for part in rel_path.parts):
        return True
    if rel_path.suffix in SKIPPED_SUFFIXES:
        return True
    if rel_path.parts[:3] == ("plugins", "eqemu-oracle", "cache"):
        return True
    if rel_path.parts[:3] == ("plugins", "eqemu-oracle", "local-extensions"):
        filename = rel_path.name
        return filename not in {"README.md", "_example.json"}
    return False


def get_bundle_root(plugin_metadata_path: Path = PLUGIN_METADATA_PATH) -> str:
    metadata = json.loads(plugin_metadata_path.read_text(encoding="utf-8"))
    version = metadata["version"]
    return f"eqemu-oracle-v{version}"


def build_release_bundle(output_dir: Path, repo_root: Path = REPO_ROOT) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    bundle_root = get_bundle_root()
    archive_path = output_dir / f"{bundle_root}.zip"
    archive_path_resolved = archive_path.resolve()

    with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(repo_root.rglob("*")):
            if path.is_dir():
                continue
            if path.resolve() == archive_path_resolved:
                continue
            rel_path = path.relative_to(repo_root)
            if _should_skip_bundle_path(rel_path):
                continue
            archive_name = Path(bundle_root, *rel_path.parts).as_posix()
            if path.name in EXECUTABLE_BUNDLE_NAMES:
                info = ZipInfo.from_file(path, archive_name)
                info.compress_type = ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = (0o755 << 16)
                archive.writestr(info, path.read_bytes())
            else:
                archive.write(path, archive_name)

    return archive_path
