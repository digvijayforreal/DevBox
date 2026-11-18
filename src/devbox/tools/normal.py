"""Lookup tools.

Most are DIRECT (`return_direct=True`): the LLM only picks the tool + arguments,
then the tool's report goes straight to the user and the turn ends. The LLM is
not called again to read or rephrase the result.

Four stay NORMAL (result goes back to the LLM) because the LLM needs the data
to continue: check_port_in_use and find_process (-> kill_process),
search_installed_programs (-> uninstall_program) and get_system_info (facts).
"""

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
from devbox.utils import Deadline, dir_size, human_size, metrics, note, report, run_cmd, table, truncate

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


@tool(return_direct=True)
def get_disk_usage() -> str:
    """Show the user used/free space for every drive on this PC. The result is shown
    directly to the user and ends your turn."""
    items = []
    for part in psutil.disk_partitions(all=False):
        try:
            u = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        items.append({"label": f"{part.mountpoint} ({part.fstype})",
                      "value": f"{human_size(u.free)} free of {human_size(u.total)} · {human_size(u.used)} used",
                      "percent": u.percent})
    return report("Disk usage", metrics("Drives", items), icon="💽")


@tool(return_direct=True)
def find_large_files(folder: str, min_size_mb: int = 100, limit: int = 20) -> str:
    """Show the user the largest files (>= min_size_mb) inside an allowed folder.
    The result is shown directly to the user and ends your turn.

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


@tool(return_direct=True)
def find_node_modules(folder: str, limit: int = 30) -> str:
    """Show the user node_modules folders inside an allowed folder with size and how long
    since the project was touched. The result is shown directly to the user and ends your turn.

    Args:
        folder: Absolute path to scan, e.g. C:\\Users\\me\\Documents\\Projects.
        limit: Max results.
    """
    try:
        root = resolve_in_allowed(folder)
    except SafetyError as e:
        return report("node_modules", note(_err(e)), icon="📁")
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
    rows = [(human_size(s), f"{int((now - m) / 86400)} days ago" if m else "?", p)
            for s, p, m in results[: max(1, limit)]]
    footer = f"{len(results)} folder(s), {human_size(sum(r[0] for r in results))} total"
    if deadline.hit:
        footer += " · scan stopped at the time limit, results are partial"
    return report(f"node_modules in {root}",
                  table("Folders", ["Size", "Project last touched", "Path"], rows, footer), icon="📁")


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


@tool(return_direct=True)
def list_running_processes(sort_by: str = "memory", limit: int = 15) -> str:
    """Show the user the top running processes by 'memory' or 'cpu'.
    The result is shown directly to the user and ends your turn.

    Args:
        sort_by: 'memory' or 'cpu'.
        limit: Number of processes.
    """
    rows = _snapshot_processes(sort_by, limit)
    return report(f"Running processes by {sort_by}",
                  table("Processes", ["PID", "Name", "CPU %", "Memory", "User"],
                        [(r["pid"], r["name"], f"{r['cpu_percent']:.1f}", human_size(r["memory"]), r["user"])
                         for r in rows]), icon="⚙️")


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
def find_process(name: str, limit: int = 10) -> str:
    """Find running processes by name and return their PIDs to you (use before kill_process).

    Args:
        name: Process name or part of it, e.g. 'chrome' or 'node.exe'.
        limit: Max matches to return.
    """
    q = name.strip().lower().removesuffix(".exe")
    if not q:
        return "Error: give a process name."
    hits = []
    for p in psutil.process_iter(["pid", "name", "memory_info", "username"]):
        pname = (p.info["name"] or "").lower()
        if p.info["pid"] == 0 or q not in pname:
            continue
        mem = p.info["memory_info"].rss if p.info["memory_info"] else 0
        hits.append({"pid": p.info["pid"], "name": p.info["name"], "memory": human_size(mem),
                     "user": (p.info.get("username") or "").split("\\")[-1],
                     "protected": pname in config.PROTECTED_PROCESSES})
    if not hits:
        return f"No running process matches '{name}'."
    hits.sort(key=lambda h: h["protected"])
    note = ""
    if all(h["protected"] for h in hits):
        note = "\nAll matches are protected Windows processes; they cannot be killed. Tell the user."
    return json.dumps(hits[: max(1, limit)], indent=2) + note


@tool(return_direct=True)
def scan_outdated_packages(project_path: str, ecosystem: str = "python") -> str:
    """Show the user the outdated packages of a project.
    The result is shown directly to the user and ends your turn.

    Args:
        project_path: Absolute path of the project folder.
        ecosystem: 'python' (uses the project's .venv via uv) or 'node' (npm outdated).
    """
    title, icon = f"Outdated {ecosystem} packages", "📦"
    try:
        proj = resolve_in_allowed(project_path)
    except SafetyError as e:
        return report(title, note(_err(e)), icon=icon)
    if not proj.is_dir():
        return report(title, note(f"Error: '{proj}' is not a folder."), icon=icon)
    title = f"Outdated {ecosystem} packages in {proj.name}"

    if ecosystem == "node":
        if not (proj / "package.json").exists():
            return report(title, note("Error: no package.json in that folder."), icon=icon)
        code, out, err = run_cmd(["npm", "outdated", "--json"], cwd=proj, timeout=180)
        # npm outdated exits with 1 when packages are outdated; that's fine.
        try:
            data = json.loads(out or "{}")
        except json.JSONDecodeError:
            return report(title, note(f"Error: {truncate((err or out).strip(), 600)}"), icon=icon)
        rows = [(name, v.get("current", "-"), v.get("wanted", "-"), v.get("latest", "-"))
                for name, v in sorted(data.items()) if isinstance(v, dict)]
        if not rows:
            return report(title, note("All npm packages are up to date."), icon=icon)
        return report(title, table("Packages", ["Package", "Current", "Wanted", "Latest"], rows,
                                   f"{len(rows)} outdated"), icon=icon)

    venv = next((proj / v for v in (".venv", "venv", "env") if (proj / v / "Scripts" / "python.exe").exists()), None)
    if venv is None:
        return report(title, note("Error: no virtualenv (.venv/venv) with python.exe found in that project."), icon=icon)
    code, out, err = run_cmd(["uv", "pip", "list", "--outdated", "--format", "json",
                              "--python", str(venv / "Scripts" / "python.exe")], cwd=proj, timeout=180)
    if code != 0:
        return report(title, note(f"Error: {err.strip()[:800]}"), icon=icon)
    try:
        data = json.loads(out or "[]")
    except json.JSONDecodeError:
        data = []
    rows = [(p.get("name", "?"), p.get("version", "-"), p.get("latest_version", "-")) for p in data]
    if not rows:
        return report(title, note("All Python packages are up to date."), icon=icon)
    return report(title, table("Packages", ["Package", "Installed", "Latest"], rows, f"{len(rows)} outdated"), icon=icon)


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
                list_running_processes, find_process, check_port_in_use, scan_outdated_packages,
                search_installed_programs]
# The subset that answers the user directly (return_direct=True).
DIRECT_LOOKUP_TOOL_NAMES = {t.name for t in NORMAL_TOOLS if t.return_direct}
