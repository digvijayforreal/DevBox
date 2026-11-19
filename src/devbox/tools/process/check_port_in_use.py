"""check_port_in_use

NORMAL (data): the result goes back to the LLM.
Used by: process agent.
"""

from __future__ import annotations

import json

import psutil
from langchain.tools import tool



@tool
def check_port_in_use(port: int) -> str:
    """Check which process is using / listening on a TCP or UDP port.

    Args:
        port: Port number, e.g. 3000.
    """
    try:
        conns = psutil.net_connections(kind="inet")
    except psutil.AccessDenied:
        return "Error: access denied reading network connections."
    hits = []
    for c in conns:
        if c.laddr and c.laddr.port == port:
            name = "?"
            if c.pid:
                try:
                    name = psutil.Process(c.pid).name()
                except psutil.Error:
                    pass
            hits.append({"pid": c.pid, "process": name, "status": c.status,
                         "local": f"{c.laddr.ip}:{c.laddr.port}",
                         "type": "TCP" if c.type == 1 else "UDP"})
    if not hits:
        return f"Port {port} is free."
    uniq = {json.dumps(h, sort_keys=True) for h in hits}
    return json.dumps([json.loads(u) for u in uniq], indent=2)
