"""scan_outdated_packages

NORMAL (direct): return_direct=True, card to the user, turn ends.
Used by: dependency agent.
"""

from __future__ import annotations

import json

from langchain.tools import tool

from devbox.core.safety import SafetyError, resolve_in_allowed
from devbox.tools.common import err as _err
from devbox.core.utils import note, report, run_cmd, table, truncate


@tool(return_direct=True)
def scan_outdated_packages(project_path: str, ecosystem: str = "python") -> str:
    """Show the outdated packages of a project.

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
