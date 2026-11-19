"""clear_cache

RISK: pauses for the user's approval before changing anything.
Used by: storage agent.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from langchain.tools import tool
from send2trash import send2trash

from devbox.core.safety import log_action
from devbox.tools.approval import ask_human
from devbox.tools.approval import rejected as _rejected
from devbox.core.utils import human_size, path_size, run_cmd


def _cache_targets() -> dict[str, dict]:
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    _, uv_dir, _ = run_cmd(["uv", "cache", "dir"], timeout=15)
    return {
        "pip": {"path": local / "pip" / "cache", "cmd": [sys.executable, "-m", "pip", "cache", "purge"]},
        "npm": {"path": local / "npm-cache", "cmd": ["npm", "cache", "clean", "--force"]},
        "uv": {"path": Path(uv_dir.strip()) if uv_dir.strip() else local / "uv" / "cache",
               "cmd": ["uv", "cache", "prune"]},
        "temp": {"path": Path(os.environ.get("TEMP", local / "Temp")), "cmd": None},
    }


def _clean_temp(temp: Path, older_than_s: int = 86400) -> tuple[int, int]:
    """Recycle top-level TEMP entries older than 1 day. Files in use are skipped."""
    removed, freed = 0, 0
    now = time.time()
    for e in os.scandir(temp):
        try:
            if now - e.stat(follow_symlinks=False).st_mtime < older_than_s:
                continue
            size = path_size(Path(e.path))
            send2trash(e.path)
            removed += 1
            freed += size
        except OSError:
            continue
    return removed, freed


@tool
def clear_cache(targets: list[str]) -> str:
    """Clear developer caches.

    Args:
        targets: Any of 'pip', 'npm', 'uv', 'temp'.
    """
    known = _cache_targets()
    chosen = [t.lower() for t in targets if t.lower() in known]
    if not chosen:
        return f"Error: unknown targets {targets}. Use: {list(known)}"
    details, total = [], 0
    for t in chosen:
        p = known[t]["path"]
        size = path_size(p) if p.exists() else 0
        total += size
        how = "files older than 1 day -> Recycle Bin" if t == "temp" else " ".join(map(str, known[t]["cmd"]))
        details.append(f"{t:<5} {human_size(size):>10}  {p}   [{how}]")
    d = ask_human("clear_cache", f"Clear {', '.join(chosen)} cache(s), about {human_size(total)}",
                  details, {"targets": chosen},
                  warning="Package caches re-download on next install. uv uses 'prune' (keeps what is in use).")
    if d["decision"] == "reject":
        return _rejected(d)
    if d["decision"] == "edit":
        chosen = [t.lower() for t in d.get("args", {}).get("targets", []) if t.lower() in known]
    results = []
    for t in chosen:
        if t == "temp":
            n, freed = _clean_temp(known[t]["path"])
            results.append(f"temp: recycled {n} items ({human_size(freed)})")
        else:
            code, out, err = run_cmd([str(c) for c in known[t]["cmd"]], timeout=300)
            results.append(f"{t}: {'ok' if code == 0 else 'failed'} {(out or err).strip()[:150]}")
    log_action(f"clear_cache {chosen}: {results}")
    return "\n".join(results)
