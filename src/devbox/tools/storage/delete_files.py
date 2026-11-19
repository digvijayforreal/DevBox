"""delete_files

RISK: pauses for the user's approval before changing anything.
Used by: storage agent.
"""

from __future__ import annotations

from pathlib import Path

from langchain.tools import tool
from send2trash import send2trash

from devbox.core.safety import SafetyError, check_deletable, log_action
from devbox.tools.approval import ask_human
from devbox.tools.approval import rejected as _rejected
from devbox.core.utils import human_size, path_size


def _preview_delete(paths: list[str]) -> tuple[list[Path], list[str], list[str], int]:
    ok, details, errors, total = [], [], [], 0
    for p in paths:
        try:
            rp = check_deletable(p)
        except SafetyError as e:
            errors.append(f"✖ skipped: {e}")
            continue
        size = path_size(rp)
        total += size
        ok.append(rp)
        details.append(f"{human_size(size):>10}  {rp}")
    return ok, details, errors, total


@tool
def delete_files(paths: list[str]) -> str:
    """Move files/folders to the Recycle Bin (recoverable).

    Args:
        paths: Absolute paths of files or folders to delete.
    """
    ok, details, errors, total = _preview_delete(paths)
    if not ok:
        return "Nothing deletable. " + " ".join(errors)
    d = ask_human("delete_files",
                  f"Move {len(ok)} item(s), {human_size(total)}, to the Recycle Bin",
                  details + errors, {"paths": [str(p) for p in ok]},
                  warning="Items go to the Recycle Bin and can be restored from there.")
    if d["decision"] == "reject":
        return _rejected(d)
    if d["decision"] == "edit":
        ok, _, errors, total = _preview_delete(d.get("args", {}).get("paths", []))
    done, failed = [], []
    for p in ok:
        try:
            send2trash(str(p))
            done.append(str(p))
        except OSError as e:
            failed.append(f"{p}: {e}")
    log_action(f"delete_files -> recycle bin: {done}")
    msg = f"Moved {len(done)} item(s) ({human_size(total)}) to the Recycle Bin."
    if failed:
        msg += f" Failed: {failed}"
    if errors:
        msg += f" Skipped: {errors}"
    return msg
