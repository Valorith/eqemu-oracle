from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .constants import CACHE_ROOT, PLUGIN_ROOT, PLUGIN_VERSION
from .utils import dump_json, ensure_dir, excerpt, load_json, short_hash, slugify


EXAMPLE_INDEX_ROOT = CACHE_ROOT / "examples"
MAX_EXAMPLE_FILES = int(os.environ.get("EQEMU_ORACLE_MAX_EXAMPLE_FILES", "2000"))
MAX_EXAMPLE_BYTES = int(os.environ.get("EQEMU_ORACLE_MAX_EXAMPLE_BYTES", "65536"))
EXAMPLE_SUFFIXES = {
    "quests": {".pl", ".lua"},
    "plugins": {".pl", ".pm"},
}


def _source_cache_path(domain: str, source: dict[str, Any]) -> Path:
    source_id = str(source.get("id") or source.get("url") or source.get("path") or "source")
    return EXAMPLE_INDEX_ROOT / domain / f"{slugify(source_id, fallback='source')}-{short_hash(_source_signature(source), 8)}.json"


def _source_signature(source: dict[str, Any]) -> str:
    stable = {
        key: source.get(key)
        for key in ("id", "url", "path", "source_type", "branch", "context_key")
        if source.get(key) is not None
    }
    return json.dumps(stable, sort_keys=True, default=str)


def _example_record(domain: str, source: dict[str, Any], relative_path: str, content: str, *, truncated: bool) -> dict[str, Any]:
    source_id = str(source.get("id") or source.get("url") or "source")
    language = "lua" if relative_path.lower().endswith(".lua") else "perl"
    record_id = f"{domain}:example:{slugify(source_id, fallback='source')}:{short_hash(relative_path, 12)}"
    title = f"{source.get('title') or source_id}: {relative_path}"
    return {
        "id": record_id,
        "domain": domain,
        "entity_type": "example-file",
        "source_id": source_id,
        "source_title": source.get("title") or source_id,
        "source_url": source.get("url"),
        "source_type": source.get("source_type"),
        "context_key": source.get("context_key"),
        "path": relative_path,
        "title": title,
        "language": language,
        "summary": excerpt(content, 360),
        "content": content,
        "content_truncated": truncated,
        "tags": source.get("tags", []),
        "provenance": {
            "effective_source": "example_source",
            "contributors": [{"source": "example_source", "id": source_id, "url": source.get("url")}],
        },
    }


def _read_example_file(path: Path) -> tuple[str, bool] | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    truncated = len(raw) > MAX_EXAMPLE_BYTES
    return raw[:MAX_EXAMPLE_BYTES].decode("utf-8", errors="replace"), truncated


def _iter_local_examples(domain: str, root: Path) -> list[tuple[str, str, bool]]:
    suffixes = EXAMPLE_SUFFIXES[domain]
    records: list[tuple[str, str, bool]] = []
    if not root.exists():
        return records
    for path in sorted(item for item in root.rglob("*") if item.is_file() and item.suffix.lower() in suffixes):
        if domain == "quests" and path.relative_to(root).parts[:1] == ("plugins",):
            continue
        loaded = _read_example_file(path)
        if loaded is None:
            continue
        content, truncated = loaded
        records.append((path.relative_to(root).as_posix(), content, truncated))
        if len(records) >= MAX_EXAMPLE_FILES:
            break
    return records


def _local_source_root(source: dict[str, Any]) -> Path | None:
    raw_path = source.get("path")
    raw_url = source.get("url")
    candidate: str | None = None
    if isinstance(raw_path, str) and raw_path.strip():
        candidate = raw_path.strip()
    elif isinstance(raw_url, str) and raw_url.startswith("file://"):
        candidate = urlparse(raw_url).path
    if not candidate:
        return None
    path = Path(candidate).expanduser()
    if not path.is_absolute():
        path = PLUGIN_ROOT / path
    return path


def _github_source_parts(source: dict[str, Any]) -> tuple[str, str, str, str] | None:
    url = source.get("url")
    if not isinstance(url, str):
        return None
    parsed = urlparse(url)
    if parsed.netloc.lower() != "github.com":
        return None
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2:
        return None
    owner = parts[0]
    repo = parts[1].removesuffix(".git")
    branch = str(source.get("branch") or "master")
    subpath = ""
    if len(parts) >= 4 and parts[2] == "tree":
        branch = parts[3]
        subpath = "/".join(parts[4:])
    elif isinstance(source.get("path"), str):
        subpath = str(source["path"]).strip("/")
    return owner, repo, branch, subpath


def _github_repo_url(owner: str, repo: str) -> str:
    return f"https://github.com/{owner}/{repo}"


def _git_default_branch(repo_url: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "ls-remote", "--symref", repo_url, "HEAD"],
            capture_output=True,
            check=False,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    for line in completed.stdout.splitlines():
        if line.startswith("ref: ") and line.endswith("\tHEAD"):
            ref = line.split()[1]
            return ref.removeprefix("refs/heads/")
    return None


def _download_github_archive(owner: str, repo: str, branch: str) -> bytes:
    url = f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{branch}"
    request = urllib.request.Request(url, headers={"User-Agent": f"eqemu-oracle/{PLUGIN_VERSION}"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def _clone_github_examples(owner: str, repo: str, branch: str, subpath: str, target_root: Path) -> Path | None:
    repo_url = _github_repo_url(owner, repo)
    clone_root = target_root / f"{repo}-{short_hash(branch + subpath, 8)}"
    command = [
        "git",
        "clone",
        "--depth",
        "1",
        "--filter=blob:none",
        "--branch",
        branch,
    ]
    if subpath.strip("/"):
        command.extend(["--sparse"])
    command.extend([repo_url, str(clone_root)])
    try:
        completed = subprocess.run(command, capture_output=True, check=False, text=True, timeout=180)
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    normalized_subpath = subpath.strip("/")
    if normalized_subpath:
        sparse = subprocess.run(
            ["git", "sparse-checkout", "set", normalized_subpath],
            cwd=str(clone_root),
            capture_output=True,
            check=False,
            text=True,
            timeout=60,
        )
        if sparse.returncode != 0:
            return None
        return clone_root / normalized_subpath
    return clone_root


def _iter_github_examples(domain: str, source: dict[str, Any]) -> list[tuple[str, str, bool]]:
    parts = _github_source_parts(source)
    if parts is None:
        return []
    owner, repo, branch, subpath = parts
    archive_bytes: bytes | None = None
    default_branch = _git_default_branch(_github_repo_url(owner, repo))
    branch_candidates = [branch, default_branch, "main", "master"]
    for candidate_branch in dict.fromkeys(candidate for candidate in branch_candidates if candidate):
        try:
            archive_bytes = _download_github_archive(owner, repo, candidate_branch)
            branch = candidate_branch
            break
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise

    suffixes = EXAMPLE_SUFFIXES[domain]
    normalized_subpath = subpath.strip("/")
    records: list[tuple[str, str, bool]] = []
    with tempfile.TemporaryDirectory(prefix="eqemu-oracle-examples-") as temp_dir:
        if archive_bytes is None:
            source_root: Path | None = None
            for candidate_branch in dict.fromkeys(candidate for candidate in branch_candidates if candidate):
                source_root = _clone_github_examples(owner, repo, candidate_branch, normalized_subpath, Path(temp_dir))
                if source_root is not None:
                    break
            if source_root is None:
                return []
            return _iter_local_examples(domain, source_root)
        archive_path = Path(temp_dir) / "source.zip"
        archive_path.write_bytes(archive_bytes)
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                if member.is_dir():
                    continue
                parts = [part for part in member.filename.split("/") if part]
                if len(parts) < 2:
                    continue
                relative_path = "/".join(parts[1:])
                if normalized_subpath:
                    if relative_path != normalized_subpath and not relative_path.startswith(f"{normalized_subpath}/"):
                        continue
                    display_path = relative_path.removeprefix(f"{normalized_subpath}/")
                else:
                    display_path = relative_path
                if domain == "quests" and display_path.split("/", 1)[0] == "plugins":
                    continue
                if Path(display_path).suffix.lower() not in suffixes:
                    continue
                raw = archive.read(member)
                truncated = len(raw) > MAX_EXAMPLE_BYTES
                content = raw[:MAX_EXAMPLE_BYTES].decode("utf-8", errors="replace")
                records.append((display_path, content, truncated))
                if len(records) >= MAX_EXAMPLE_FILES:
                    break
    return records


def _index_source(domain: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    local_root = _local_source_root(source)
    if local_root is not None:
        examples = _iter_local_examples(domain, local_root)
    else:
        examples = _iter_github_examples(domain, source)
    return [
        _example_record(domain, source, relative_path, content, truncated=truncated)
        for relative_path, content, truncated in examples
    ]


def ensure_example_indexes(domain: str, sources: list[dict[str, Any]]) -> bool:
    if domain not in EXAMPLE_SUFFIXES:
        return False
    ensure_dir(EXAMPLE_INDEX_ROOT / domain)
    changed = False
    for source in sources:
        index_path = _source_cache_path(domain, source)
        source_id = str(source.get("id") or source.get("url") or source.get("path") or "source")
        for stale_path in index_path.parent.glob(f"{slugify(source_id, fallback='source')}-*.json"):
            if stale_path != index_path:
                stale_path.unlink(missing_ok=True)
                changed = True
        signature = _source_signature(source)
        if index_path.exists():
            try:
                cached = load_json(index_path)
            except (OSError, json.JSONDecodeError):
                cached = {}
            if cached.get("source_signature") == signature and not cached.get("error"):
                continue
        error: str | None = None
        try:
            records = _index_source(domain, source)
        except Exception as exc:
            records = []
            error = str(exc)
        dump_json(
            index_path,
            {
                "source_signature": signature,
                "source_hash": hashlib.sha256(signature.encode("utf-8")).hexdigest(),
                "source": source,
                "error": error,
                "records": records,
            },
        )
        changed = True
    return changed


def load_example_records(domain: str) -> list[dict[str, Any]]:
    root = EXAMPLE_INDEX_ROOT / domain
    if not root.exists():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        try:
            payload = load_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        items = payload.get("records", [])
        if isinstance(items, list):
            records.extend(item for item in items if isinstance(item, dict))
    return records


def example_index_digest() -> str:
    inputs: list[dict[str, Any]] = []
    for path in sorted(EXAMPLE_INDEX_ROOT.rglob("*.json")) if EXAMPLE_INDEX_ROOT.exists() else []:
        try:
            stat = path.stat()
        except OSError:
            continue
        inputs.append(
            {
                "path": path.relative_to(EXAMPLE_INDEX_ROOT).as_posix(),
                "size": stat.st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode("utf-8")).hexdigest()
