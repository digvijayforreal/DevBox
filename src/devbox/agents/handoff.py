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

from devbox.core import config
from devbox.agents.registry import AGENT_DESCRIPTIONS


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
        if count > 0 and runtime.state.get("handoff_from") == target:
            # Stop ping-pong: the target just handed this request to us in this same turn.
            return (f"Do not hand back to {target}: it just handed this request to you. Handle it "
                    "yourself with YOUR TOOLS. If it is blocked by the safety rules (protected or "
                    "not-allowed path, protected process), tell the user briefly why it can't be done.")

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
                "handoff_from": source,
                "messages": [last_ai, ack],
            },
        )

    return handoff


def handoff_tools_for(source: str, targets: list[str]):
    return [make_handoff_tool(source, t) for t in targets if t != source]
