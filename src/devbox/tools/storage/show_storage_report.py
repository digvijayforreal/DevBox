"""show_storage_report

NORMAL (direct): card to the user; agents/direct.py decides when it ends the turn.
Used by: storage agent.
"""

from __future__ import annotations

import os
from pathlib import Path

import psutil
from langchain.tools import tool

from devbox.core import config
from devbox.core.safety import SafetyError, resolve_in_allowed
from devbox.core.utils import Deadline, dir_size, human_size, metrics, note, report, table


@tool
def show_storage_report(folder: str = "") -> str:
    """Show a storage report: all drives + biggest items inside a folder.

    Args:
        folder: Folder whose children are measured. Empty = all allowed folders.
    """
    drives = []
    for part in psutil.disk_partitions(all=False):
        try:
            u = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        drives.append({"label": part.mountpoint,
                       "value": f"{human_size(u.used)} used · {human_size(u.free)} free of {human_size(u.total)}",
                       "percent": u.percent})
    drive_sec = metrics("Drives", drives)

    try:
        targets = [resolve_in_allowed(folder)] if folder.strip() else list(config.ALLOWED_ROOTS)
    except SafetyError as e:
        return report("Storage report", drive_sec, note(f"Error: {e}"), icon="💽")

    deadline = Deadline(config.SCAN_TIME_LIMIT_S * 2)
    sizes: list[tuple[int, str, int]] = []
    for t in targets:
        if not t.is_dir():
            continue
        try:
            children = list(os.scandir(t))
        except OSError:
            continue
        for e in children:
            if deadline.expired():
                break
            try:
                if e.is_dir(follow_symlinks=False):
                    s, n = dir_size(Path(e.path), deadline)
                else:
                    s, n = e.stat().st_size, 1
                sizes.append((s, e.path, n))
            except OSError:
                continue
    sizes.sort(reverse=True)
    rows = [(human_size(s), f"{n:,}", p) for s, p, n in sizes[:15]]
    footer = "Scan hit the time limit; sizes may be partial." if deadline.hit else None
    scope = ", ".join(str(t) for t in targets)
    return report("Storage report", drive_sec,
                  table(f"Biggest items in {scope}", ["Size", "Files", "Path"], rows, footer), icon="💽")
