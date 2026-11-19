"""Triage agent: understands the request and hands it to the right specialist."""

from devbox.agents.base import AgentSpec
from devbox.prompts.triage import PROMPT
from devbox.tools.triage.get_system_info import get_system_info
from devbox.tools.triage.show_system_info import show_system_info

AGENT = AgentSpec(
    name="triage",
    description="the TRIAGE agent (general questions / unclear requests)",
    prompt=PROMPT,
    data_tools=[get_system_info],
    direct_tools=[show_system_info],
)
