"""list_running_processes

NORMAL (direct): return_direct=True, card to the user, turn ends.
Used by: process agent.
"""

from __future__ import annotations


from langchain.tools import tool

from devbox.tools.process._snapshot import snapshot_processes as _snapshot_processes
from devbox.core.utils import human_size, report, table


@tool(return_direct=True)
def list_running_processes(sort_by: str = "memory", limit: int = 15) -> str:
    """Show the top running processes by 'memory' or 'cpu'.

    Args:
        sort_by: 'memory' or 'cpu'.
        limit: Number of processes.
    """
    rows = _snapshot_processes(sort_by, limit)
    return report(f"Running processes by {sort_by}",
                  table("Processes", ["PID", "Name", "CPU %", "Memory", "User"],
                        [(r["pid"], r["name"], f"{r['cpu_percent']:.1f}", human_size(r["memory"]), r["user"])
                         for r in rows]), icon="⚙️")
