"""Process sampling shared by list_running_processes and show_process_table."""

from __future__ import annotations

import time

import psutil


def snapshot_processes(sort_by: str, limit: int) -> list[dict]:
    procs = list(psutil.process_iter(["pid", "name", "memory_info", "username"]))
    for p in procs:
        try:
            p.cpu_percent(None)
        except psutil.Error:
            pass
    time.sleep(0.5)
    ncpu = psutil.cpu_count() or 1
    rows = []
    for p in procs:
        if p.info["pid"] == 0:  # "System Idle Process" is idle time, not a real process
            continue
        try:
            cpu = p.cpu_percent(None) / ncpu
            mem = p.info["memory_info"].rss if p.info["memory_info"] else 0
            rows.append({"pid": p.info["pid"], "name": p.info["name"] or "?",
                         "cpu_percent": round(cpu, 1), "memory": mem,
                         "user": (p.info.get("username") or "").split("\\")[-1]})
        except psutil.Error:
            continue
    key = "cpu_percent" if sort_by == "cpu" else "memory"
    rows.sort(key=lambda r: r[key], reverse=True)
    return rows[: max(1, limit)]
