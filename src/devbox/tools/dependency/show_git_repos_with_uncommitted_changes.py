"""show_git_repos_with_uncommitted_changes

NORMAL (direct): return_direct=True, card to the user, turn ends.
Used by: dependency, storage agents.
"""

from __future__ import annotations

import os
from pathlib import Path

from langchain.tools import tool

from devbox.core import config
from devbox.core.safety import SafetyError, resolve_in_allowed
from devbox.core.utils import Deadline, note, report, run_cmd, table


@tool(return_direct=True)
def show_git_repos_with_uncommitted_changes(folder: str, max_depth: int = 4) -> str:
    """Show every git repo under a folder that has uncommitted or unpushed work. Use before deleting projects.

    Args:
        folder: Absolute folder to search.
        max_depth: How deep to search for repos.
    """
    try:
        root = resolve_in_allowed(folder)
    except SafetyError as e:
        return report("Git repos", note(f"Error: {e}"), icon="🌿")
    repos: list[Path] = []
    deadline = Deadline(config.SCAN_TIME_LIMIT_S)
    stack = [(root, 0)]
    while stack and not deadline.expired():
        cur, depth = stack.pop()
        if (cur / ".git").exists():
            repos.append(cur)
            continue
        if depth >= max_depth:
            continue
        try:
            for e in os.scandir(cur):
                if e.is_dir(follow_symlinks=False) and e.name not in ("node_modules", ".venv", "venv", "__pycache__"):
                    stack.append((Path(e.path), depth + 1))
        except OSError:
            continue

    if not repos:
        return report("Git repos", note(f"No git repositories found under {root}."), icon="🌿")
    rows, clean = [], 0
    for repo in sorted(repos):
        code, out, _ = run_cmd(["git", "-C", str(repo), "status", "--porcelain", "--branch"], timeout=30)
        if code != 0:
            continue
        lines = out.splitlines()
        branch = lines[0][3:] if lines and lines[0].startswith("##") else "?"
        changed = [ln for ln in lines[1:] if ln.strip()]
        ahead = ""
        if "[ahead" in branch:
            ahead = branch[branch.index("[ahead") + 7:].split("]")[0].split(",")[0]
        no_upstream = "..." not in branch
        if changed or ahead or no_upstream:
            rows.append((str(repo), branch.split("...")[0], len(changed), ahead or "0",
                         "no remote / upstream" if no_upstream else ""))
        else:
            clean += 1
    return report(f"Repos with unsaved work under {root}",
                  table("Repositories", ["Repo", "Branch", "Uncommitted files", "Unpushed commits", "Note"], rows,
                        footer=f"{len(rows)} need attention, {clean} clean."), icon="🌿")
