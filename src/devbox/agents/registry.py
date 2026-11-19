"""All agents in one place.

To add an agent:
  1. prompts/<name>.py        -> PROMPT = "..."
  2. tools/<name>/<tool>.py   -> one file per tool
  3. agents/<name>.py         -> AGENT = AgentSpec(...) listing its tools by type
  4. add it to AGENT_SPECS below
"""

from __future__ import annotations

from devbox.agents import dependency, process, storage, triage
from devbox.agents.base import AgentSpec

AGENT_SPECS: list[AgentSpec] = [triage.AGENT, storage.AGENT, process.AGENT, dependency.AGENT]

AGENTS = [a.name for a in AGENT_SPECS]
BY_NAME = {a.name: a for a in AGENT_SPECS}
AGENT_TOOLS = {a.name: a.tools for a in AGENT_SPECS}
AGENT_DESCRIPTIONS = {a.name: a.description for a in AGENT_SPECS}

# Tool type sets used by the graph, the UI and the activity log.
DIRECT_TOOL_NAMES = {t.name for a in AGENT_SPECS for t in a.direct_tools}  # card to user, turn ends
ACTION_TOOL_NAMES = {t.name for a in AGENT_SPECS for t in a.risk_tools}    # needs approval
DATA_TOOL_NAMES = {t.name for a in AGENT_SPECS for t in a.data_tools}      # result back to the LLM
