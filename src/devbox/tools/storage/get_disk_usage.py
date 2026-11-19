"""get_disk_usage

NORMAL (direct): card to the user; agents/direct.py decides when it ends the turn.
Used by: storage agent.
"""

from __future__ import annotations


import psutil
from langchain.tools import tool

from devbox.core.utils import human_size, metrics, report


@tool
def get_disk_usage() -> str:
    """Show used/free space for every drive on this PC."""
    items = []
    for part in psutil.disk_partitions(all=False):
        try:
            u = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        items.append({"label": f"{part.mountpoint} ({part.fstype})",
                      "value": f"{human_size(u.free)} free of {human_size(u.total)} · {human_size(u.used)} used",
                      "percent": u.percent})
    return report("Disk usage", metrics("Drives", items), icon="💽")
