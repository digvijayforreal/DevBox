"""uninstall_program

RISK: pauses for the user's approval before changing anything.
Used by: dependency agent.
"""

from __future__ import annotations


from langchain.tools import tool

from devbox.core.safety import log_action
from devbox.tools.approval import ask_human
from devbox.tools.approval import rejected as _rejected
from devbox.core.utils import run_cmd, truncate


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
