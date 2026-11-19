"""show_vulnerable_dependencies

NORMAL (direct): return_direct=True, card to the user, turn ends.
Used by: dependency agent.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from langchain.tools import tool

from devbox.core.safety import SafetyError, resolve_in_allowed
from devbox.core.utils import note, report, run_cmd, table


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
    """Show known security vulnerabilities (CVEs) in a project's Python and/or npm dependencies.

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
