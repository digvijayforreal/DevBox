"""find_process

NORMAL (data): the result goes back to the LLM.
Used by: process agent.
"""

from __future__ import annotations

import json

import psutil
from langchain.tools import tool

from devbox.core import config
from devbox.core.utils import human_size


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
