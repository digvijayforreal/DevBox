"""kill_process

RISK: pauses for the user's approval before changing anything.
Used by: process agent.
"""

from __future__ import annotations

import time

import psutil
from langchain.tools import tool

from devbox.core.safety import SafetyError, check_killable, log_action
from devbox.tools.approval import ask_human
from devbox.tools.approval import rejected as _rejected
from devbox.core.utils import human_size


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
