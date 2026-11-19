"""Process agent: CPU/RAM, running processes, ports, killing processes."""

from devbox.agents.base import AgentSpec
from devbox.prompts.process import PROMPT
from devbox.tools.process.check_port_in_use import check_port_in_use
from devbox.tools.process.find_process import find_process
from devbox.tools.process.kill_process import kill_process
from devbox.tools.process.list_running_processes import list_running_processes
from devbox.tools.process.show_process_table import show_process_table
from devbox.tools.triage.get_system_info import get_system_info  # shared with triage
from devbox.tools.triage.show_system_info import show_system_info  # shared with triage

AGENT = AgentSpec(
    name="process",
    description="the PROCESS agent (CPU/RAM, running processes, ports, killing processes)",
    prompt=PROMPT,
    data_tools=[find_process, check_port_in_use, get_system_info],
    direct_tools=[list_running_processes, show_process_table, show_system_info],
    risk_tools=[kill_process],
)
