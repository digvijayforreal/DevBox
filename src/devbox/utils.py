"""Small shared helpers: sizes, tables, folder walking, subprocess."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Iterable

from devbox import config


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"


# --- Structured reports (TYPE A output) ----------------------------------------
# Show tools return a JSON "report" the web UI renders as cards, meters and tables.
# The same JSON is what the LLM sees in later turns, so numbers stay identical.

def table(title: str, columns: list[str], rows: Iterable[Iterable[object]],
          footer: str | None = None) -> dict:
    return {"type": "table", "title": title, "columns": columns,
            "rows": [[str(c) for c in r] for r in rows], "footer": footer}


def metrics(title: str, items: list[dict[str, Any]], footer: str | None = None) -> dict:
    """items: [{"label": str, "value": str, "percent": float | None}]"""
    return {"type": "metrics", "title": title, "items": items, "footer": footer}


def note(text: str) -> dict:
    return {"type": "text", "title": "", "text": text}


def report(title: str, *sections: dict | None, icon: str = "📋", live: str | None = None) -> str:
    return json.dumps({"kind": "report", "title": title, "icon": icon, "live": live,
                       "sections": [s for s in sections if s]}, ensure_ascii=False)


def truncate(text: str, limit: int = config.LLM_TOOL_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated {len(text) - limit} chars]"


class Deadline:
    def __init__(self, seconds: float):
        self.end = time.monotonic() + seconds
        self.hit = False

    def expired(self) -> bool:
        if time.monotonic() > self.end:
            self.hit = True
        return self.hit


def dir_size(path: Path, deadline: Deadline | None = None) -> tuple[int, int]:
    """Return (bytes, file_count) for a folder using an iterative scandir walk."""
    total, count = 0, 0
    stack = [path]
    while stack:
        if deadline and deadline.expired():
            break
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                            count += 1
                    except OSError:
                        continue
        except OSError:
            continue
    return total, count


def path_size(path: Path) -> int:
    try:
        if path.is_dir():
            return dir_size(path, Deadline(config.SCAN_TIME_LIMIT_S))[0]
        return path.stat().st_size
    except OSError:
        return 0


def run_cmd(args: list[str], cwd: str | Path | None = None, timeout: int = 120) -> tuple[int, str, str]:
    """Run a command without a shell. Resolves .cmd/.exe shims on Windows."""
    exe = shutil.which(args[0])
    if exe is None:
        return 127, "", f"'{args[0]}' not found on PATH"
    try:
        proc = subprocess.run(
            [exe, *args[1:]], cwd=cwd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout}s"
    except OSError as e:
        return 1, "", str(e)
