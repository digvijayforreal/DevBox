"""search_installed_programs

NORMAL (data): the result goes back to the LLM.
Used by: dependency agent.
"""

from __future__ import annotations


from langchain.tools import tool

from devbox.core.utils import run_cmd, truncate


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
