"""Agent-to-agent handoff tools (LangChain "multiple agent subgraphs" pattern).

A handoff tool returns Command(graph=Command.PARENT, goto=<agent>) which makes the
*parent* graph jump to another agent node. It also:
  * sets `active_agent` (persisted by the checkpointer -> next user turn goes
    straight to that specialist),
  * stores the reason in `handoff_note` (injected into the next agent's prompt),
  * returns the AIMessage that called the tool + a matching ToolMessage so the
    conversation history stays valid.
"""

from __future__ import annotations

from langchain.messages import AIMessage, ToolMessage
from langchain.tools import ToolRuntime, tool
from langgraph.types import Command

from devbox import config

AGENT_DESCRIPTIONS = {
    "triage": "the TRIAGE agent (general questions / unclear requests)",
    "storage": "the STORAGE agent (disk space, large files, node_modules, caches, deleting files)",
    "process": "the PROCESS agent (CPU/RAM, running processes, ports, killing processes)",
    "dependency": "the DEPENDENCY agent (outdated packages, vulnerabilities, git repos, updates, uninstalling programs)",
}


def make_handoff_tool(source: str, target: str):
    name = f"transfer_to_{target}"

    @tool(name, description=f"Hand the conversation to {AGENT_DESCRIPTIONS[target]}.")
    def handoff(reason: str, runtime: ToolRuntime) -> Command | str:
        """
        Args:
            reason: One sentence: what the user needs from the next agent.
        """
        count = int(runtime.state.get("handoff_count", 0) or 0)
        if count >= config.MAX_HANDOFFS_PER_TURN:
            return "Handoff limit reached for this turn. Answer the user directly with what you know."

        last_ai = next(m for m in reversed(runtime.state["messages"]) if isinstance(m, AIMessage))
        ack = ToolMessage(
            content=f"Transferred from {source} to {target}. Reason: {reason}",
            tool_call_id=runtime.tool_call_id,
            name=name,
        )
        return Command(
            graph=Command.PARENT,
            goto=target,
            update={
                "active_agent": target,
                "handoff_note": f"The {source} agent handed this conversation to you. Reason: {reason}",
                "handoff_count": count + 1,
                "messages": [last_ai, ack],
            },
        )

    return handoff


def handoff_tools_for(source: str, targets: list[str]):
    return [make_handoff_tool(source, t) for t in targets if t != source]
