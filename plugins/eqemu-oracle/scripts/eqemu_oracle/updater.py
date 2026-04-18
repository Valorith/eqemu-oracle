from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from .constants import BASE_ROOT, MERGED_ROOT, REPO_ROOT
from .dataset import write_merged_dataset


def _run_command(args: list[str], cwd: Path) -> str:
    completed = subprocess.run(
        args,
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _git(args: list[str], cwd: Path) -> str:
    return _run_command(["git", *args], cwd)


def _current_branch(repo_root: Path) -> str | None:
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], repo_root).strip()
    return None if branch == "HEAD" else branch


def _local_branch_exists(repo_root: Path, branch: str) -> bool:
    try:
        _git(["show-ref", "--verify", f"refs/heads/{branch}"], repo_root)
    except subprocess.CalledProcessError:
        return False
    return True


def _checkout_branch(repo_root: Path, *, remote: str, branch: str) -> None:
    if _local_branch_exists(repo_root, branch):
        _git(["switch", branch], repo_root)
        return
    _git(["switch", "-c", branch, "--track", f"{remote}/{branch}"], repo_root)


def rebuild_committed_dataset() -> dict[str, Any]:
    if MERGED_ROOT.exists():
        shutil.rmtree(MERGED_ROOT)
    return write_merged_dataset(BASE_ROOT, MERGED_ROOT)


def update_plugin_repo(
    repo_root: Path | None = None,
    *,
    remote: str = "origin",
    branch: str | None = None,
    allow_dirty: bool = False,
    skip_rebuild: bool = False,
    restore_branch: bool = False,
) -> dict[str, Any]:
    root = repo_root or REPO_ROOT
    root = Path(root)

    _git(["rev-parse", "--is-inside-work-tree"], root)
    remote_url = _git(["config", "--get", f"remote.{remote}.url"], root).strip()
    current_branch = _current_branch(root)
    target_branch = branch or current_branch or "main"
    status = _git(["status", "--porcelain"], root)
    dirty = bool(status.strip())
    if dirty and not allow_dirty:
        raise RuntimeError("Refusing to update plugin repo with a dirty worktree. Commit/stash changes or pass --allow-dirty.")

    _git(["fetch", remote], root)
    checked_out_branch = current_branch
    switched_branches = False
    restored_branch = False
    if current_branch != target_branch:
        _checkout_branch(root, remote=remote, branch=target_branch)
        checked_out_branch = target_branch
        switched_branches = True
    try:
        before_commit = _git(["rev-parse", "HEAD"], root).strip()
        pull_output = _git(["pull", "--ff-only", remote, target_branch], root)
        after_commit = _git(["rev-parse", "HEAD"], root).strip()

        result: dict[str, Any] = {
            "repo_root": str(root),
            "remote": remote,
            "remote_url": remote_url,
            "branch": target_branch,
            "current_branch": current_branch,
            "checked_out_branch": checked_out_branch,
            "switched_branches": switched_branches,
            "restore_branch_requested": restore_branch,
            "restored_branch": False,
            "before_commit": before_commit,
            "after_commit": after_commit,
            "code_changed": before_commit != after_commit,
            "dirty_worktree": dirty,
            "pull_output": pull_output,
            "rebuild": {"ran": False},
        }

        if not skip_rebuild:
            result["rebuild"] = {
                "ran": True,
                "manifest": rebuild_committed_dataset(),
            }
    finally:
        if restore_branch and switched_branches and current_branch:
            _git(["switch", current_branch], root)
            restored_branch = True

    result["restored_branch"] = restored_branch

    return result
