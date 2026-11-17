"""NORMAL tools: results go back to the LLM, which reasons over them and replies."""

from __future__ import annotations

import json
import os
import platform
import time
from datetime import datetime
from pathlib import Path

import psutil
from langchain.tools import tool

from devbox import config
from devbox.safety import SafetyError, resolve_in_allowed
from devbox.utils import Deadline, dir_size, human_size, run_cmd, truncate

SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "__pycache__", ".cache", "$recycle.bin"}


def _err(e: Exception) -> str:
    return f"Error: {e}"


# ---------------------------------------------------------------------------
@tool
def get_system_info() -> str:
    """Get OS, CPU, RAM, uptime, allowed folders and installed Python/Node versions."""
    vm = psutil.virtual_memory()
    boot = datetime.fromtimestamp(psutil.boot_time())
    uptime_h = (time.time() - psutil.boot_time()) / 3600
    _, node_v, _ = run_cmd(["node", "--version"], timeout=10)
    _, uv_v, _ = run_cmd(["uv", "--version"], timeout=10)
    info = {
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "cpu": platform.processor(),
        "cores_logical": psutil.cpu_count(),
        "cpu_percent_now": psutil.cpu_percent(interval=0.3),
        "ram_total": human_size(vm.total),
        "ram_used_percent": vm.percent,
        "boot_time": boot.isoformat(timespec="minutes"),
        "uptime_hours": round(uptime_h, 1),
        "python": platform.python_version(),
        "node": node_v.strip() or "not installed",
        "uv": uv_v.strip() or "not installed",
        "allowed_folders": [str(p) for p in config.ALLOWED_ROOTS],
        "home": str(config.HOME),
    }
    return json.dumps(info, indent=2)


@tool
def get_disk_usage() -> str:
    """Get used/free space for every drive on this PC."""
    rows = []
    for part in psutil.disk_partitions(all=False):
        try:
            u = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        rows.append({
            "drive": part.mountpoint, "fs": part.fstype,
            "total": human_size(u.total), "used": human_size(u.used),
            "free": human_size(u.free), "percent_used": u.percent,
        })
    return json.dumps(rows, indent=2)


@tool
def find_large_files(folder: str, min_size_mb: int = 100, limit: int = 20) -> str:
    """Find the largest files (>= min_size_mb) inside an allowed folder.

    Args:
        folder: Absolute path to scan, e.g. C:\\Users\\me\\Downloads.
        min_size_mb: Minimum file size in MB.
        limit: Max files to return.
    """
    try:
        root = resolve_in_allowed(folder)
    except SafetyError as e:
        return _err(e)
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
    out = [{
        "path": p, "size": human_size(s),
        "last_modified": datetime.fromtimestamp(m).date().isoformat(),
    } for s, p, m in found[: max(1, limit)]]
    note = " (scan stopped at time limit, results partial)" if deadline.hit else ""
    return truncate(json.dumps({"folder": str(root), "note": note, "files": out}, indent=2))


@tool
def find_node_modules(folder: str, limit: int = 30) -> str:
    """Find node_modules folders inside an allowed folder with size and how long since the project was touched.

    Args:
        folder: Absolute path to scan, e.g. C:\\Users\\me\\Documents\\Projects.
        limit: Max results.
    """
    try:
        root = resolve_in_allowed(folder)
    except SafetyError as e:
        return _err(e)
    deadline = Deadline(config.SCAN_TIME_LIMIT_S * 2)
    results = []
    stack = [root]
    while stack and not deadline.expired():
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    if not e.is_dir(follow_symlinks=False) or e.is_symlink():
                        continue
                    if e.name == "node_modules":
                        project = Path(cur)
                        size, _ = dir_size(Path(e.path), deadline)
                        marker = project / "package.json"
                        try:
                            mtime = (marker if marker.exists() else project).stat().st_mtime
                        except OSError:
                            mtime = 0
                        results.append((size, e.path, mtime))
                    elif e.name.lower() not in SKIP_DIRS:
                        stack.append(Path(e.path))
        except OSError:
            continue
    results.sort(reverse=True)
    now = time.time()
    out = [{
        "path": p, "size": human_size(s),
        "project_last_touched_days_ago": int((now - m) / 86400) if m else None,
    } for s, p, m in results[: max(1, limit)]]
    total = human_size(sum(r[0] for r in results))
    note = "partial (time limit hit)" if deadline.hit else "complete"
    return truncate(json.dumps({"count": len(results), "total_size": total,
                                "scan": note, "items": out}, indent=2))


def _snapshot_processes(sort_by: str, limit: int) -> list[dict]:
    procs = list(psutil.process_iter(["pid", "name", "memory_info", "username"]))
    for p in procs:
        try:
            p.cpu_percent(None)
        except psutil.Error:
            pass
    time.sleep(0.5)
    ncpu = psutil.cpu_count() or 1
    rows = []
    for p in procs:
        if p.info["pid"] == 0:  # "System Idle Process" is idle time, not a real process
            continue
        try:
            cpu = p.cpu_percent(None) / ncpu
            mem = p.info["memory_info"].rss if p.info["memory_info"] else 0
            rows.append({"pid": p.info["pid"], "name": p.info["name"] or "?",
                         "cpu_percent": round(cpu, 1), "memory": mem,
                         "user": (p.info.get("username") or "").split("\\")[-1]})
        except psutil.Error:
            continue
    key = "cpu_percent" if sort_by == "cpu" else "memory"
    rows.sort(key=lambda r: r[key], reverse=True)
    return rows[: max(1, limit)]


@tool
def list_running_processes(sort_by: str = "memory", limit: int = 15) -> str:
    """List top running processes by 'memory' or 'cpu'.

    Args:
        sort_by: 'memory' or 'cpu'.
        limit: Number of processes.
    """
    rows = _snapshot_processes(sort_by, limit)
    for r in rows:
        r["memory"] = human_size(r["memory"])
    return json.dumps(rows, indent=2)


@tool
def check_port_in_use(port: int) -> str:
    """Check which process is using / listening on a TCP or UDP port.

    Args:
        port: Port number, e.g. 3000.
    """
    try:
        conns = psutil.net_connections(kind="inet")
    except psutil.AccessDenied:
        return "Error: access denied reading network connections."
    hits = []
    for c in conns:
        if c.laddr and c.laddr.port == port:
            name = "?"
            if c.pid:
                try:
                    name = psutil.Process(c.pid).name()
                except psutil.Error:
                    pass
            hits.append({"pid": c.pid, "process": name, "status": c.status,
                         "local": f"{c.laddr.ip}:{c.laddr.port}",
                         "type": "TCP" if c.type == 1 else "UDP"})
    if not hits:
        return f"Port {port} is free."
    uniq = {json.dumps(h, sort_keys=True) for h in hits}
    return json.dumps([json.loads(u) for u in uniq], indent=2)


@tool
def scan_outdated_packages(project_path: str, ecosystem: str = "python") -> str:
    """List outdated packages for a project.

    Args:
        project_path: Absolute path of the project folder.
        ecosystem: 'python' (uses the project's .venv via uv) or 'node' (npm outdated).
    """
    try:
        proj = resolve_in_allowed(project_path)
    except SafetyError as e:
        return _err(e)
    if not proj.is_dir():
        return f"Error: '{proj}' is not a folder."
    if ecosystem == "node":
        if not (proj / "package.json").exists():
            return "Error: no package.json in that folder."
        code, out, err = run_cmd(["npm", "outdated", "--json"], cwd=proj, timeout=180)
        # npm outdated exits with 1 when packages are outdated; that's fine.
        return truncate(out.strip() or err.strip() or "All npm packages are up to date.")
    venv = next((proj / v for v in (".venv", "venv", "env") if (proj / v / "Scripts" / "python.exe").exists()), None)
    if venv is None:
        return "Error: no virtualenv (.venv/venv) with python.exe found in that project."
    code, out, err = run_cmd(["uv", "pip", "list", "--outdated", "--format", "json",
                              "--python", str(venv / "Scripts" / "python.exe")], cwd=proj, timeout=180)
    if code != 0:
        return f"Error: {err.strip()[:800]}"
    return truncate(out.strip() if out.strip() not in ("", "[]") else "All Python packages are up to date.")


@tool
def search_installed_programs(query: str) -> str:
    """Search installed Windows programs via winget to get the exact package Id (needed before uninstalling).

    Args:
        query: Part of the program name, e.g. 'zoom'.
    """
    code, out, err = run_cmd(["winget", "list", "--query", query, "--accept-source-agreements",
                              "--disable-interactivity"], timeout=90)
    if code == 127:
        return "Error: winget is not available on this PC."
    text = out.strip() or err.strip()
    lines = [ln for ln in text.splitlines() if ln.strip() and not set(ln.strip()) <= set("-\\|/ ")]
    return truncate("\n".join(lines) or "No installed program matched.")


NORMAL_TOOLS = [get_system_info, get_disk_usage, find_large_files, find_node_modules,
                list_running_processes, check_port_in_use, scan_outdated_packages,
                search_installed_programs]
