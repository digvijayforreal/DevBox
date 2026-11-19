"""Shared approval helper for RISK tools (human-in-the-loop via LangGraph `interrupt()`).

Each risk tool:
  1. validates input with the safety layer,
  2. builds an exact preview of what will happen,
  3. calls ask_human(preview) -> the whole graph pauses, the UI shows an approval dialog,
  4. resumes with the user's decision: approve / edit (new args) / reject.

NOTE: on resume LangGraph re-runs the tool from the top, so everything before
ask_human() must be side-effect free (only reads + validation).
"""

from __future__ import annotations

from typing import Any

from langgraph.types import interrupt


def ask_human(tool_name: str, summary: str, details: list[str], args: dict[str, Any],
              warning: str = "") -> dict:
    """Pause the graph and wait for approve / edit / reject."""
    decision = interrupt({
        "tool": tool_name,
        "summary": summary,
        "details": details,
        "args": args,
        "warning": warning,
    })
    if not isinstance(decision, dict):
        decision = {"decision": str(decision)}
    decision.setdefault("decision", "reject")
    return decision


def rejected(d: dict) -> str:
    reason = d.get("reason") or "no reason given"
    return f"User REJECTED this action ({reason}). Do not retry it unless the user asks."
