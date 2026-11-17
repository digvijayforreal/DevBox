"""TYPE B tools (review & continue): human-in-the-loop via LangGraph `interrupt()`.

Each tool:
  1. validates input with the safety layer,
  2. builds an exact preview of what will happen,
  3. calls interrupt(preview) -> the whole graph pauses, the UI shows an approval dialog,
  4. resumes with the user's decision: approve / edit (new args) / reject.

NOTE: on resume LangGraph re-runs the tool from the top, so everything before
interrupt() must be side-effect free (it is: only reads + validation).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import psutil
from langchain.tools import tool
from langgraph.types import interrupt
from send2trash import send2trash

from devbox.safety import SafetyError, check_deletable, check_killable, log_action, resolve_in_allowed
from devbox.utils import human_size, path_size, run_cmd, truncate


def ask_human(tool_name: str, summary: str, details: list[str], args: dict[str, Any],
              warning: str = "") -> dict:
    """Pause the graph and wait for approve / edit / reject."""
    decision = interrupt({
        "tool": tool_name,
        "summary": summary,
        "details": details,
        "args": args,
        "warning": warning,
    })
    if not isinstance(decision, dict):
        decision = {"decision": str(decision)}
    decision.setdefault("decision", "reject")
    return decision


def _rejected(d: dict) -> str:
    reason = d.get("reason") or "no reason given"
    return f"User REJECTED this action ({reason}). Do not retry it unless the user asks."


# ---------------------------------------------------------------------------
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
    """Move files/folders to the Recycle Bin (recoverable). REQUIRES user approval.

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


# ---------------------------------------------------------------------------
@tool
def kill_process(pid: int) -> str:
    """Terminate a process by PID. REQUIRES user approval.

    Args:
        pid: Process ID.
    """
    try:
        proc = check_killable(pid)
        info = proc.as_dict(["name", "exe", "cmdline", "memory_info", "username", "create_time"])
    except SafetyError as e:
        return f"Error: {e}"
    cmd = " ".join(info.get("cmdline") or [])[:200]
    details = [
        f"Name:    {info.get('name')}",
        f"PID:     {pid}",
        f"Exe:     {info.get('exe')}",
        f"Command: {cmd}",
        f"Memory:  {human_size(info['memory_info'].rss) if info.get('memory_info') else '?'}",
        f"Started: {time.strftime('%Y-%m-%d %H:%M', time.localtime(info.get('create_time') or 0))}",
    ]
    d = ask_human("kill_process", f"Terminate {info.get('name')} (PID {pid})", details, {"pid": pid},
                  warning="Unsaved work in that program will be lost.")
    if d["decision"] == "reject":
        return _rejected(d)
    if d["decision"] == "edit":
        try:
            pid = int(d.get("args", {}).get("pid", pid))
            proc = check_killable(pid)
        except (SafetyError, ValueError) as e:
            return f"Error after edit: {e}"
    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
            result = "terminated"
        except psutil.TimeoutExpired:
            proc.kill()
            result = "force-killed (did not exit in 5s)"
    except psutil.Error as e:
        return f"Error killing PID {pid}: {e}"
    log_action(f"kill_process pid={pid} name={info.get('name')} -> {result}")
    return f"Process {info.get('name')} (PID {pid}) {result}."


# ---------------------------------------------------------------------------
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
    """Clear developer caches. REQUIRES user approval.

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


# ---------------------------------------------------------------------------
@tool
def update_package(package: str, project_path: str, ecosystem: str = "python") -> str:
    """Upgrade one package inside a project. REQUIRES user approval.

    Args:
        package: Package name, e.g. 'requests' or 'react'.
        project_path: Absolute project folder.
        ecosystem: 'python' (project's .venv via uv) or 'node' (npm).
    """
    try:
        proj = resolve_in_allowed(project_path)
    except SafetyError as e:
        return f"Error: {e}"
    venv_py = next((proj / v / "Scripts" / "python.exe" for v in (".venv", "venv", "env")
                    if (proj / v / "Scripts" / "python.exe").exists()), None)
    if ecosystem != "node" and venv_py is None:
        return "Error: no virtualenv found in that project."

    def build_cmd(pkg: str) -> list[str] | None:
        # Package names (optionally pinned, e.g. 'requests==2.32.3' / '@scope/pkg') only.
        if not pkg or not all(c.isalnum() or c in "-_.@/=<>~" for c in pkg) or pkg.startswith("-"):
            return None
        if ecosystem == "node":
            return ["npm", "install", pkg if "@" in pkg[1:] else f"{pkg}@latest"]
        return ["uv", "pip", "install", "--upgrade", pkg, "--python", str(venv_py)]

    cmd = build_cmd(package)
    if cmd is None:
        return "Error: invalid package name."
    d = ask_human("update_package", f"Upgrade '{package}' in {proj.name}",
                  [f"Project: {proj}", f"Command: {' '.join(cmd)}"],
                  {"package": package},
                  warning="Major upgrades can break your project. Commit first.")
    if d["decision"] == "reject":
        return _rejected(d)
    if d["decision"] == "edit":
        package = str(d.get("args", {}).get("package", package))
        cmd = build_cmd(package)
        if cmd is None:
            return "Error after edit: invalid package name."
    code, out, err = run_cmd(cmd, cwd=proj, timeout=600)
    log_action(f"update_package {package} in {proj}: exit={code}")
    return truncate(f"exit code {code}\n{(out + err).strip()}", 1500)


# ---------------------------------------------------------------------------
@tool
def uninstall_program(package_id: str) -> str:
    """Uninstall a Windows program by its exact winget Id (get it from search_installed_programs). REQUIRES user approval.

    Args:
        package_id: Exact winget package Id, e.g. 'Zoom.Zoom'.
    """
    code, out, err = run_cmd(["winget", "list", "--id", package_id, "--exact",
                              "--accept-source-agreements", "--disable-interactivity"], timeout=90)
    if code == 127:
        return "Error: winget not available."
    if package_id.lower() not in out.lower():
        return f"Error: no installed program with exact Id '{package_id}'. Use search_installed_programs first."
    lines = [ln for ln in out.splitlines() if ln.strip() and not set(ln.strip()) <= set("-\\|/ ")]
    d = ask_human("uninstall_program", f"Uninstall '{package_id}'", lines[-3:],
                  {"package_id": package_id},
                  warning="The program's own uninstaller may open. This is not reversible from DevBox.")
    if d["decision"] == "reject":
        return _rejected(d)
    if d["decision"] == "edit":
        package_id = str(d.get("args", {}).get("package_id", package_id))
    code, out, err = run_cmd(["winget", "uninstall", "--id", package_id, "--exact",
                              "--accept-source-agreements", "--disable-interactivity"], timeout=900)
    log_action(f"uninstall_program {package_id}: exit={code}")
    return truncate(f"exit code {code}\n{(out + err).strip()}", 1500)


ACTION_TOOLS = [delete_files, kill_process, clear_cache, update_package, uninstall_program]
ACTION_TOOL_NAMES = {t.name for t in ACTION_TOOLS}
