from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from .constants import PLUGIN_METADATA_PATH, REPO_ROOT


SKIPPED_ROOTS = {".git", "dist", "dist-local-smoke"}


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
            if rel_path.parts and rel_path.parts[0] in SKIPPED_ROOTS:
                continue
            archive.write(path, Path(bundle_root, *rel_path.parts).as_posix())

    return archive_path
