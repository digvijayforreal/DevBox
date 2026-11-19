"""update_package

RISK: pauses for the user's approval before changing anything.
Used by: dependency agent.
"""

from __future__ import annotations


from langchain.tools import tool

from devbox.core.safety import SafetyError, log_action, resolve_in_allowed
from devbox.tools.approval import ask_human
from devbox.tools.approval import rejected as _rejected
from devbox.core.utils import run_cmd, truncate


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
