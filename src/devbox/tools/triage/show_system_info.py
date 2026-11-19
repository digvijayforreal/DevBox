"""show_system_info

NORMAL (direct): return_direct=True, card to the user, turn ends.
Used by: triage, process agents.
"""

from __future__ import annotations


from langchain.tools import tool

from devbox.core.system_report import build_system_report


@tool(return_direct=True)
def show_system_info() -> str:
    """Show their system details: CPU usage (total + per core), RAM, swap, disks,
    machine info, uptime and top CPU processes. Use whenever the user asks about system
    details, specs, CPU usage, RAM usage or 'how is my PC doing'."""
    return build_system_report()
