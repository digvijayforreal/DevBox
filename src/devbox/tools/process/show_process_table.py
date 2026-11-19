"""show_process_table

NORMAL (direct): card to the user; agents/direct.py decides when it ends the turn.
Used by: process agent.
"""

from __future__ import annotations


import psutil
from langchain.tools import tool

from devbox.tools.process._snapshot import snapshot_processes as _snapshot_processes
from devbox.core.utils import human_size, metrics, report, table


@tool
def show_process_table(sort_by: str = "memory", limit: int = 15) -> str:
    """Show a table of top processes by 'memory' or 'cpu'.

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
