"""LangChain callback handler that turns every LLM/tool step into an activity event.

Events are delivered to `sink(event: dict)` (the web UI) and written to
logs/devbox_activity.log. It also feeds the active provider's usage tracker and waits
before requests that would break the RPM/TPM limits.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any, Callable
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import ToolMessage

from devbox import config
from devbox.tools import ACTION_TOOL_NAMES, SHOW_TOOL_NAMES
from devbox.usage import tracker

Sink = Callable[[dict], None]


def tool_kind(name: str) -> str:
    if name.startswith("transfer_to_"):
        return "handoff"
    if name in SHOW_TOOL_NAMES:
        return "show"
    if name in ACTION_TOOL_NAMES:
        return "action"
    return "normal"


def _agent_from(tags: list[str] | None) -> str:
    for t in tags or []:
        if t.startswith("agent:"):
            return t.split(":", 1)[1]
    return "?"


class ActivityCallback(BaseCallbackHandler):
    raise_error = False

    def __init__(self, sink: Sink | None = None) -> None:
        self.sink = sink
        self.starts: dict[UUID, float] = {}
        self.names: dict[UUID, str] = {}
        from devbox import llm

        sel = llm.current()  # Session updates these whenever the model is (re)built
        self.provider, self.model = sel.provider, sel.model

    @property
    def usage(self):
        return tracker(self.provider)

    # -- helpers -----------------------------------------------------------
    def emit(self, kind: str, **data: Any) -> None:
        event = {"ts": datetime.now().strftime("%H:%M:%S"), "kind": kind, **data}
        try:
            with config.ACTIVITY_LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, default=str) + "\n")
        except OSError:
            pass
        if self.sink:
            try:
                self.sink(event)
            except Exception:  # UI must never break the agent
                pass

    # -- LLM -----------------------------------------------------------------
    def on_chat_model_start(self, serialized, messages, *, run_id, tags=None, **kw):
        agent = _agent_from(tags)
        waited = self.usage.wait_for_slot(
            on_wait=lambda d, why: self.emit("throttle", agent=agent, seconds=round(d, 1), reason=why))
        self.starts[run_id] = time.time()
        self.emit("llm_start", agent=agent, messages=len(messages[0]) if messages else 0,
                  waited=round(waited, 1), provider=self.usage.p["label"], model=self.model)

    def on_llm_end(self, response, *, run_id, tags=None, **kw):
        latency = time.time() - self.starts.pop(run_id, time.time())
        in_t = out_t = 0
        calls: list[str] = []
        try:
            msg = response.generations[0][0].message
            um = getattr(msg, "usage_metadata", None) or {}
            in_t, out_t = int(um.get("input_tokens", 0)), int(um.get("output_tokens", 0))
            calls = [c["name"] for c in (getattr(msg, "tool_calls", None) or [])]
        except (IndexError, AttributeError):
            pass
        self.usage.record_success(in_t, out_t, latency)
        self.emit("llm_end", agent=_agent_from(tags), latency=round(latency, 2),
                  input_tokens=in_t, output_tokens=out_t, tool_calls=calls)

    def on_llm_error(self, error, *, run_id, tags=None, **kw):
        self.starts.pop(run_id, None)
        is_429 = self.usage.record_error(error)
        self.emit("rate_limited" if is_429 else "llm_error", agent=_agent_from(tags), error=str(error)[:300])

    # -- Tools ---------------------------------------------------------------
    def on_tool_start(self, serialized, input_str, *, run_id, tags=None, inputs=None, **kw):
        name = (serialized or {}).get("name") or kw.get("name") or "?"
        self.starts[run_id] = time.time()
        self.names[run_id] = name
        args = inputs if isinstance(inputs, dict) else input_str
        self.emit("tool_start", agent=_agent_from(tags), tool=name, tool_kind=tool_kind(name),
                  args=str(args)[:200])

    def on_tool_end(self, output, *, run_id, tags=None, **kw):
        name = self.names.pop(run_id, "?")
        dur = time.time() - self.starts.pop(run_id, time.time())
        if isinstance(output, ToolMessage):
            preview = str(output.content)
        elif type(output).__name__ == "Command":
            preview = f"Command(goto={getattr(output, 'goto', '')})"
        else:
            preview = str(output)
        if tool_kind(name) == "show":
            try:
                rep = json.loads(preview)
                preview = f"report shown to user: {rep.get('title', '')}"
            except (ValueError, AttributeError):
                pass
        self.emit("tool_end", agent=_agent_from(tags), tool=name, tool_kind=tool_kind(name),
                  ms=int(dur * 1000), preview=preview[:160].replace("\n", " "))

    def on_tool_error(self, error, *, run_id, tags=None, **kw):
        name = self.names.pop(run_id, "?")
        self.starts.pop(run_id, None)
        etype = type(error).__name__
        if etype in ("GraphInterrupt", "NodeInterrupt"):
            self.emit("interrupt", agent=_agent_from(tags), tool=name)
        elif etype == "ParentCommand":
            self.emit("tool_end", agent=_agent_from(tags), tool=name, tool_kind="handoff",
                      ms=0, preview="handoff command")
        else:
            self.emit("tool_error", agent=_agent_from(tags), tool=name, error=str(error)[:200])
