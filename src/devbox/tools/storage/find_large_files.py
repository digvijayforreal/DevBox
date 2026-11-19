"""find_large_files

NORMAL (direct): return_direct=True, card to the user, turn ends.
Used by: storage agent.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from langchain.tools import tool

from devbox.core import config
from devbox.core.safety import SafetyError, resolve_in_allowed
from devbox.tools.common import err as _err
from devbox.core.utils import Deadline, human_size, note, report, table


@tool(return_direct=True)
def find_large_files(folder: str, min_size_mb: int = 100, limit: int = 20) -> str:
    """Show the largest files (>= min_size_mb) inside an allowed folder.

    Args:
        folder: Absolute path to scan, e.g. C:\\Users\\me\\Downloads.
        min_size_mb: Minimum file size in MB.
        limit: Max files to return.
    """
    try:
        root = resolve_in_allowed(folder)
    except SafetyError as e:
        return report("Large files", note(_err(e)), icon="📦")
    min_bytes = max(1, min_size_mb) * 1024 * 1024
    deadline = Deadline(config.SCAN_TIME_LIMIT_S)
    found: list[tuple[int, str, float]] = []
    stack = [root]
    while stack and not deadline.expired():
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    try:
                        if e.is_symlink():
                            continue
                        if e.is_dir(follow_symlinks=False):
                            stack.append(Path(e.path))
                        else:
                            st = e.stat(follow_symlinks=False)
                            if st.st_size >= min_bytes:
                                found.append((st.st_size, e.path, st.st_mtime))
                    except OSError:
                        continue
        except OSError:
            continue
    found.sort(reverse=True)
    rows = [(human_size(s), datetime.fromtimestamp(m).date().isoformat(), p)
            for s, p, m in found[: max(1, limit)]]
    footer = f"{len(found)} file(s) ≥ {min_size_mb} MB"
    if deadline.hit:
        footer += " · scan stopped at the time limit, results are partial"
    return report(f"Files ≥ {min_size_mb} MB in {root}",
                  table("Largest files", ["Size", "Modified", "Path"], rows, footer), icon="📦")
