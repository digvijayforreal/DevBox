"""On-demand system details (CPU, RAM, disks, machine info, top processes).

Used by the `show_system_info` tool (type A) and by GET /api/system for the
card's "Live" refresh. Nothing polls this unless the user asked for it.
"""

from __future__ import annotations

import platform
import socket
import time
from datetime import datetime

import psutil

from devbox.core.utils import human_size, metrics, report, table

_ME = psutil.Process()


def _uptime() -> str:
    secs = int(time.time() - psutil.boot_time())
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    return f"{d}d {h}h {rem // 60}m"


def build_system_report() -> str:
    _ME.cpu_percent(None)
    per_core = psutil.cpu_percent(interval=0.6, percpu=True)
    cpu_total = sum(per_core) / max(len(per_core), 1)
    ncpu = psutil.cpu_count() or 1
    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()
    freq = psutil.cpu_freq()

    usage = [
        {"label": "CPU", "value": f"{cpu_total:.0f}%  ·  {ncpu} threads"
                                  + (f" @ {freq.current / 1000:.2f} GHz" if freq and freq.current else ""),
         "percent": cpu_total},
        {"label": "Memory", "value": f"{human_size(vm.used)} / {human_size(vm.total)}", "percent": vm.percent},
        {"label": "Swap / page file", "value": f"{human_size(sw.used)} / {human_size(sw.total)}",
         "percent": sw.percent},
    ]
    for part in psutil.disk_partitions(all=False):
        try:
            du = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        usage.append({"label": f"Disk {part.mountpoint}",
                      "value": f"{human_size(du.free)} free of {human_size(du.total)}", "percent": du.percent})

    cores = [{"label": f"Core {i}", "value": f"{p:.0f}%", "percent": p} for i, p in enumerate(per_core)]

    # Top processes by CPU (sampled over the same window as per_core above).
    # cpu_percent needs two readings on the SAME Process object: prime, wait, read.
    procs = [p for p in psutil.process_iter(["pid", "name", "memory_info"]) if p.info["pid"] != 0]
    for p in procs:
        try:
            p.cpu_percent(None)
        except psutil.Error:
            pass
    time.sleep(0.5)
    sampled = []
    for p in procs:
        try:
            mem = p.info["memory_info"].rss if p.info["memory_info"] else 0
            sampled.append((p.cpu_percent(None) / ncpu, p.info["pid"], p.info["name"] or "?", mem))
        except psutil.Error:
            continue
    sampled.sort(reverse=True)
    top = [(pid, name, f"{cpu:.1f}%", human_size(mem)) for cpu, pid, name, mem in sampled[:6]]

    try:
        with _ME.oneshot():
            me_cpu = _ME.cpu_percent(None) / ncpu
            me_mem = _ME.memory_info().rss
            me_threads = _ME.num_threads()
    except psutil.Error:
        me_cpu, me_mem, me_threads = 0.0, 0, 0

    machine = [
        ("Computer", socket.gethostname()),
        ("OS", f"{platform.system()} {platform.release()} (build {platform.version()})"),
        ("CPU", platform.processor() or "?"),
        ("Cores", f"{psutil.cpu_count(logical=False)} physical / {ncpu} logical"),
        ("Memory", human_size(vm.total)),
        ("Uptime", _uptime()),
        ("Booted", datetime.fromtimestamp(psutil.boot_time()).strftime("%Y-%m-%d %H:%M")),
        ("Python", platform.python_version()),
        ("DevBox process", f"CPU {me_cpu:.1f}% · RAM {human_size(me_mem)} · {me_threads} threads"),
    ]

    return report(
        "System details",
        metrics("Usage", usage),
        metrics("CPU per core", cores),
        table("Machine", ["Property", "Value"], machine),
        table("Top processes by CPU", ["PID", "Name", "CPU", "Memory"], top),
        icon="🖥", live="system",
    )
