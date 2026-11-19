r"""Custom "return direct": OUR rule decides when a tool's result ends the turn.

Replaces LangChain's built-in `@tool(return_direct=True)` (always ends, even on errors).

How it works: `create_agent` runs  model -> tools -> model -> ...  This middleware runs
just BEFORE each model call. Right after a tool ran, it looks at the tool's result and calls
should_end(). If True it jumps to the end of the turn, so the model is NOT called again and
the tool's result is the answer the user sees.

    model -> tools -> [should_end()?] -- yes --> END   (tool answers the user)
                             \--------- no  --> model (LLM reads the result)
"""

from __future__ import annotations

import json
import re

from langchain.agents.middleware import before_model
from langchain.messages import AIMessage, HumanMessage, ToolMessage

# Tools that normally answer the user directly with a card (filled from the registry).
DIRECT_TOOLS: set[str] = set()

# Data tools whose simple answers need no extra LLM call.
SIMPLE_ANSWERS = {
    "check_port_in_use": ("is free.",),
    "find_process": ("No running process matches",),
    "search_installed_programs": ("No installed program matched.",),
}

# "... and delete it", "... then kill it", "if not, free it up": the user wants a next step.
_FOLLOW_UP = re.compile(r"\b(then|if not|and (delete|remove|kill|free|clear|stop|uninstall|update|upgrade))\b",
                        re.IGNORECASE)


def _is_error(result: str) -> bool:
    if result.startswith("Error"):
        return True
    try:  # report cards: {"kind": "report", "sections": [{"type": "text", "text": "Error: ..."}]}
        rep = json.loads(result)
        return any(s.get("type") == "text" and str(s.get("text", "")).startswith("Error")
                   for s in rep.get("sections", []))
    except (ValueError, AttributeError, TypeError):
        return False


def should_end(tool_name: str, result: str, user_text: str) -> tuple[bool, str]:
    """True = the tool's result is the final answer. Rules are checked top to bottom."""
    if _is_error(result):
        return False, "error: let the LLM explain it"
    if any(result.strip().endswith(s) or result.startswith(s) for s in SIMPLE_ANSWERS.get(tool_name, ())):
        return True, "simple answer: no extra LLM call needed"
    if tool_name in DIRECT_TOOLS and _FOLLOW_UP.search(user_text):
        return False, "user asked for a next step: keep going"
    if tool_name in DIRECT_TOOLS:
        return True, "direct tool: card goes straight to the user"
    return False, "the LLM needs the data"


def _tool_results(messages: list) -> list[ToolMessage]:
    """Tool results produced since the model's last message (empty if the model hasn't called a tool)."""
    out = []
    for m in reversed(messages):
        if isinstance(m, ToolMessage):
            out.append(m)
        else:
            break
    return out


@before_model(can_jump_to=["end"], name="CustomReturnDirect")
def custom_return_direct(state, runtime):
    messages = state["messages"]
    results = _tool_results(messages)
    if not results or not isinstance(messages[-len(results) - 1], AIMessage):
        return None  # not right after a tool call: let the model run
    user_text = next((m.content for m in reversed(messages)
                      if isinstance(m, HumanMessage) and isinstance(m.content, str)), "")
    # With several tools in one step, end only if every result says so.
    if all(should_end(r.name or "", str(r.content), user_text)[0] for r in results):
        return {"jump_to": "end"}
    return None
