"""get_system_info

NORMAL (data): the result goes back to the LLM.
Used by: triage, process agents.
"""

from __future__ import annotations

import json
import platform
import time
from datetime import datetime

import psutil
from langchain.tools import tool

from devbox.core import config
from devbox.core.utils import human_size, run_cmd


@tool
def get_system_info() -> str:
    """Get OS, CPU, RAM, uptime, allowed folders and installed Python/Node versions."""
    vm = psutil.virtual_memory()
    boot = datetime.fromtimestamp(psutil.boot_time())
    uptime_h = (time.time() - psutil.boot_time()) / 3600
    _, node_v, _ = run_cmd(["node", "--version"], timeout=10)
    _, uv_v, _ = run_cmd(["uv", "--version"], timeout=10)
    info = {
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "cpu": platform.processor(),
        "cores_logical": psutil.cpu_count(),
        "cpu_percent_now": psutil.cpu_percent(interval=0.3),
        "ram_total": human_size(vm.total),
        "ram_used_percent": vm.percent,
        "boot_time": boot.isoformat(timespec="minutes"),
        "uptime_hours": round(uptime_h, 1),
        "python": platform.python_version(),
        "node": node_v.strip() or "not installed",
        "uv": uv_v.strip() or "not installed",
        "allowed_folders": [str(p) for p in config.ALLOWED_ROOTS],
        "home": str(config.HOME),
    }
    return json.dumps(info, indent=2)
