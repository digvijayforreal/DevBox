"""UI-independent session runner: sends a user turn, handles approvals, collects output."""

from __future__ import annotations

import uuid
from typing import Callable

from langchain.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from langgraph.checkpoint.memory import InMemorySaver

from devbox import llm
from devbox.agents.graph import build_graph, text_of
from devbox.callbacks import ActivityCallback, Sink
from devbox.tools import ACTION_TOOL_NAMES, SHOW_TOOL_NAMES

# ask(payload) -> {"decision": "approve" | "edit" | "reject", "args": {...}, "reason": "..."}
AskFn = Callable[[dict], dict]


class Session:
    def __init__(self, sink: Sink | None = None, model=None) -> None:
        self.callback = ActivityCallback(sink)
        self.checkpointer = InMemorySaver()  # shared across model switches -> chat survives
        self.thread_id = str(uuid.uuid4())
        self.selection: llm.Selection | None = None
        self._fixed_model = model  # tests pass a fake model
        self.graph = build_graph(model, self.checkpointer) if model is not None else None

    def ensure_graph(self) -> None:
        """(Re)build the graph if the model selected in Settings changed."""
        if self._fixed_model is not None:
            return
        sel = llm.resolve(llm.current())
        if self.graph is None or sel != self.selection:
            self.graph = build_graph(llm.make_model(sel), self.checkpointer)
            self.selection = sel
            self.callback.provider, self.callback.model = sel.provider, sel.model

    @property
    def config(self) -> dict:
        sel = self.selection
        return {
            "configurable": {"thread_id": self.thread_id},
            "callbacks": [self.callback],
            "recursion_limit": 60,
            # Used by LangSmith (if enabled): readable trace name, filterable tags, and
            # thread_id groups every turn of one chat into a single LangSmith thread.
            "run_name": "DevBox turn",
            "tags": ["devbox", *( [sel.provider, sel.model] if sel else [] )],
            "metadata": {"thread_id": self.thread_id, "app": "devbox",
                         "provider": sel.provider if sel else "test",
                         "model": sel.model if sel else "fake"},
        }

    def reset(self) -> None:
        self.thread_id = str(uuid.uuid4())

    @property
    def active_agent(self) -> str:
        if self.graph is None:
            return "triage"
        return self.graph.get_state(self.config).values.get("active_agent") or "triage"

    def run(self, text: str, ask: AskFn) -> list[dict]:
        self.ensure_graph()
        before = len(self.graph.get_state(self.config).values.get("messages", []))
        self.callback.emit("user", text=text[:120])
        self.graph.invoke({"messages": [HumanMessage(content=text)], "handoff_count": 0}, self.config)

        while True:
            snap = self.graph.get_state(self.config)
            if not snap.interrupts:
                break
            decisions = {}
            for intr in snap.interrupts:
                payload = intr.value if isinstance(intr.value, dict) else {"summary": str(intr.value)}
                self.callback.emit("approval_wait", tool=payload.get("tool", "?"),
                                   summary=payload.get("summary", ""))
                decision = ask(payload)
                self.callback.emit("approval_" + decision.get("decision", "reject"),
                                   tool=payload.get("tool", "?"))
                decisions[intr.id] = decision
            self.graph.invoke(Command(resume=decisions), self.config)

        messages = self.graph.get_state(self.config).values.get("messages", [])[before:]
        return to_outputs(messages)


def to_outputs(messages: list) -> list[dict]:
    """Convert new graph messages into UI items."""
    out: list[dict] = []
    for m in messages:
        if isinstance(m, ToolMessage):
            name = m.name or ""
            if name in SHOW_TOOL_NAMES:
                out.append({"type": "show", "tool": name, "text": text_of(m)})
            elif name.startswith("transfer_to_"):
                out.append({"type": "handoff", "tool": name, "text": text_of(m)})
            elif name in ACTION_TOOL_NAMES:
                out.append({"type": "action", "tool": name, "text": text_of(m)})
        elif isinstance(m, AIMessage) and not m.tool_calls:
            t = text_of(m).strip()
            if t:
                out.append({"type": "ai", "text": t, "agent": m.name or ""})
    return out
