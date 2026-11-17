"""TYPE A tools (show & stop): return_direct=True.

The agent loop ends right after these run and the exact report goes to the user
(rendered as cards/tables in the web UI). The LLM never rewrites the numbers.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import psutil
from langchain.tools import tool

from devbox import config
from devbox.safety import SafetyError, resolve_in_allowed
from devbox.system_report import build_system_report
from devbox.tools.normal import _snapshot_processes
from devbox.utils import Deadline, dir_size, human_size, metrics, note, report, run_cmd, table


@tool(return_direct=True)
def show_system_info() -> str:
    """Show the user their system details: CPU usage (total + per core), RAM, swap, disks,
    machine info, uptime and top CPU processes. Use whenever the user asks about system
    details, specs, CPU usage, RAM usage or 'how is my PC doing'."""
    return build_system_report()


@tool(return_direct=True)
def show_storage_report(folder: str = "") -> str:
    """Show the user a storage report: all drives + biggest items inside a folder.

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


@tool(return_direct=True)
def show_process_table(sort_by: str = "memory", limit: int = 15) -> str:
    """Show the user a table of top processes by 'memory' or 'cpu'.

    Args:
        sort_by: 'memory' or 'cpu'.
        limit: Number of rows.
    """
    # cpu_percent(None) measures since the previous call; the first call is always 0.0.
    # Prime it, then read it after the ~0.5 s process sample so both cover the same window.
    psutil.cpu_percent(None)
    rows = _snapshot_processes(sort_by, limit)
    cpu = psutil.cpu_percent(None)
    vm = psutil.virtual_memory()
    return report(
        f"Top processes by {sort_by}",
        metrics("System", [
            {"label": "CPU", "value": f"{cpu:.0f}%", "percent": cpu},
            {"label": "Memory", "value": f"{human_size(vm.used)} / {human_size(vm.total)}", "percent": vm.percent},
        ]),
        table("Processes", ["PID", "Name", "CPU %", "Memory", "User"],
              [(r["pid"], r["name"], f"{r['cpu_percent']:.1f}", human_size(r["memory"]), r["user"]) for r in rows]),
        icon="⚙️",
    )


def _python_audit(proj: Path) -> list[tuple]:
    args = [sys.executable, "-m", "pip_audit", "-f", "json", "--progress-spinner", "off"]
    if (proj / "requirements.txt").exists():
        args += ["-r", str(proj / "requirements.txt")]
    else:
        site = next((proj / v / "Lib" / "site-packages" for v in (".venv", "venv", "env")
                     if (proj / v / "Lib" / "site-packages").exists()), None)
        if site is None:
            return [("python", "-", "-", "no requirements.txt or venv found", "-")]
        args += ["--path", str(site)]
    code, out, err = run_cmd(args, cwd=proj, timeout=300)
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return [("python", "-", "-", f"pip-audit failed: {(err or out).strip()[:200]}", "-")]
    rows = []
    for dep in data.get("dependencies", []):
        for v in dep.get("vulns", []):
            rows.append(("python", f"{dep['name']} {dep.get('version', '')}", v.get("id", "?"),
                         (v.get("description") or "")[:120], ", ".join(v.get("fix_versions", [])) or "none"))
    return rows


def _node_audit(proj: Path) -> list[tuple]:
    code, out, err = run_cmd(["npm", "audit", "--json"], cwd=proj, timeout=300)
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return [("node", "-", "-", f"npm audit failed: {(err or out).strip()[:200]}", "-")]
    rows = []
    for name, v in (data.get("vulnerabilities") or {}).items():
        via = v.get("via", [])
        title = next((x.get("title", "") for x in via if isinstance(x, dict)), "")
        fix = v.get("fixAvailable")
        fix_s = fix.get("version", "yes") if isinstance(fix, dict) else ("yes" if fix else "none")
        rows.append(("node", f"{name} {v.get('range', '')}", v.get("severity", "?").upper(), title[:120], fix_s))
    return rows


@tool(return_direct=True)
def show_vulnerable_dependencies(project_path: str) -> str:
    """Show the user known security vulnerabilities (CVEs) in a project's Python and/or npm dependencies.

    Args:
        project_path: Absolute path of the project folder.
    """
    try:
        proj = resolve_in_allowed(project_path)
    except SafetyError as e:
        return report("Vulnerability scan", note(f"Error: {e}"), icon="🛡")
    rows: list[tuple] = []
    checked = []
    if any((proj / f).exists() for f in ("requirements.txt", "pyproject.toml", ".venv", "venv")):
        rows += _python_audit(proj)
        checked.append("python (pip-audit)")
    if (proj / "package-lock.json").exists():
        rows += _node_audit(proj)
        checked.append("node (npm audit)")
    if not checked:
        return report("Vulnerability scan", note(f"No Python or npm project files found in {proj}."), icon="🛡")
    if not rows:
        return report("Vulnerability scan",
                      note(f"No known vulnerabilities in {proj}. Checked: {', '.join(checked)}."), icon="🛡")
    return report(f"Vulnerable dependencies in {proj.name}",
                  table("Findings", ["Ecosystem", "Package", "ID / Severity", "Summary", "Fixed in"], rows,
                        footer=f"Checked: {', '.join(checked)}"), icon="🛡")


@tool(return_direct=True)
def show_git_repos_with_uncommitted_changes(folder: str, max_depth: int = 4) -> str:
    """Show the user every git repo under a folder that has uncommitted or unpushed work. Use before deleting projects.

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


SHOW_TOOLS = [show_system_info, show_storage_report, show_process_table, show_vulnerable_dependencies,
              show_git_repos_with_uncommitted_changes]
SHOW_TOOL_NAMES = {t.name for t in SHOW_TOOLS}
