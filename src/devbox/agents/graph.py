"""Builds the multi-agent graph.

    START ──route by active_agent──► triage | storage | process | dependency
       each agent node = create_agent(...) with its own tools + transfer_to_* tools
       handoff  : tool returns Command(graph=PARENT, goto=<agent>)   -> jump to agent
       type A   : show_* tool has return_direct=True                -> agent stops, table to user
       type B   : action tool calls interrupt(preview)              -> graph pauses for approval
       finished : node returns Command(goto=END)
"""

from __future__ import annotations

import json
import re
from typing import Any

from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import ModelRequest, dynamic_prompt
from langchain.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from typing_extensions import NotRequired

from devbox import config
from devbox.agents.handoff import handoff_tools_for
from devbox.agents.prompts import PROMPTS
from devbox.tools import ACTION_TOOL_NAMES, SHOW_TOOL_NAMES
from devbox.tools import actions as A
from devbox.tools import normal as N
from devbox.tools import show as S
from devbox.utils import truncate

AGENTS = ["triage", "storage", "process", "dependency"]

AGENT_TOOLS = {
    "triage": [N.get_system_info, S.show_system_info],
    "storage": [N.get_disk_usage, N.find_large_files, N.find_node_modules, S.show_storage_report,
                S.show_git_repos_with_uncommitted_changes, A.delete_files, A.clear_cache],
    "process": [N.list_running_processes, N.find_process, N.check_port_in_use, N.get_system_info,
                S.show_system_info, S.show_process_table, A.kill_process],
    "dependency": [N.scan_outdated_packages, N.search_installed_programs,
                   S.show_vulnerable_dependencies, S.show_git_repos_with_uncommitted_changes,
                   A.update_package, A.uninstall_program],
}


class DevBoxState(AgentState):
    active_agent: NotRequired[str]
    handoff_note: NotRequired[str]
    handoff_count: NotRequired[int]
    handoff_from: NotRequired[str]  # agent that handed the current request over (this turn)


def text_of(msg: Any) -> str:
    """Extract plain text from a message whose content may be a list of parts."""
    content = msg.content
    if isinstance(content, str):
        return content
    parts = []
    for p in content or []:
        if isinstance(p, str):
            parts.append(p)
        elif isinstance(p, dict) and p.get("type") == "text":
            parts.append(p.get("text", ""))
    return "".join(parts)


MAX_TURNS = 6            # user turns of history sent to the LLM
RECENT_REPORT_ROWS = 12  # rows kept from the most recent report (for follow-ups like "delete the biggest one")


def summarize_report(text: str, full: bool) -> str:
    """Compact text for a report already shown to the user.

    full=False -> one line: "[Shown to user: <title> (table: 2 rows)]"
    full=True  -> title + up to RECENT_REPORT_ROWS rows as "a | b | c" (no JSON, no padding)
    """
    try:
        rep = json.loads(text)
        if rep.get("kind") != "report":
            raise ValueError
    except (ValueError, TypeError):
        return f"[Shown to user] {truncate(text, 300 if not full else 1200)}"

    sections = rep.get("sections", [])
    if not full:
        parts = []
        for s in sections:
            if s.get("type") == "table":
                parts.append(f"{s.get('title', 'table')}: {len(s.get('rows', []))} rows")
            elif s.get("type") == "metrics":
                parts.append(f"{s.get('title', 'metrics')}: {len(s.get('items', []))} items")
            elif s.get("type") == "text":
                parts.append(truncate(s.get("text", ""), 120))
        return f"[Shown to user: {rep.get('title', '')} ({'; '.join(parts)})]"

    lines = [f"[Shown to user: {rep.get('title', '')}]"]
    for s in sections:
        if s.get("type") == "table":
            rows = s.get("rows", [])
            lines.append(f"{s.get('title', '')}: " + " | ".join(s.get("columns", [])))
            lines += [" | ".join(r) for r in rows[:RECENT_REPORT_ROWS]]
            if len(rows) > RECENT_REPORT_ROWS:
                lines.append(f"... {len(rows) - RECENT_REPORT_ROWS} more rows")
        elif s.get("type") == "metrics":
            lines.append(f"{s.get('title', '')}: " + "; ".join(f"{i['label']} {i['value']}" for i in s.get("items", [])))
        elif s.get("type") == "text":
            lines.append(s.get("text", ""))
    return truncate("\n".join(lines), 1500)


def build_context(messages: list, max_turns: int = MAX_TURNS) -> list:
    """Context engineering for handoffs.

    Each agent gets a clean, small history:
      * only the last `max_turns` user turns,
      * user messages + final AI answers,
      * the MOST RECENT report as compact rows (so follow-ups can refer to it),
        older reports as a one-line summary,
      * short action results.
    Tool-call chatter from other agents (and handoff pairs) is dropped so the LLM
    never sees calls to tools it doesn't own.
    """
    # Keep only the last N user turns.
    human_idx = [i for i, m in enumerate(messages) if isinstance(m, HumanMessage)]
    if len(human_idx) > max_turns:
        messages = messages[human_idx[-max_turns]:]
    last_report = max((i for i, m in enumerate(messages)
                       if isinstance(m, ToolMessage) and m.name in SHOW_TOOL_NAMES), default=-1)

    out: list = []

    def add_ai(text: str) -> None:
        if out and isinstance(out[-1], AIMessage):
            out[-1] = AIMessage(content=text_of(out[-1]) + "\n\n" + text)
        else:
            out.append(AIMessage(content=text))

    for i, m in enumerate(messages):
        if isinstance(m, HumanMessage):
            out.append(HumanMessage(content=text_of(m)))
        elif isinstance(m, AIMessage):
            if m.tool_calls or (m.response_metadata or {}).get("devbox_route"):
                continue  # tool chatter and auto-route notes are not shown to the next agent
            t = text_of(m).strip()
            if t and not _looks_like_tool_json(t):  # never let the model copy text-written tool calls
                add_ai(truncate(t, 800))
        elif isinstance(m, ToolMessage):
            if m.name in SHOW_TOOL_NAMES:
                add_ai(summarize_report(text_of(m), full=(i == last_report)))
            elif m.name in ACTION_TOOL_NAMES:
                add_ai(f"[{m.name} result] {truncate(text_of(m), 300)}")
    while out and not isinstance(out[0], HumanMessage):
        out.pop(0)
    return out


def _is_empty_reply(new_messages: list) -> bool:
    """True when the agent's turn ended with an AI message that has no text and no tool call."""
    if not new_messages:
        return True
    last = new_messages[-1]
    return isinstance(last, AIMessage) and not last.tool_calls and not text_of(last).strip()


_ALL_TOOL_NAMES = sorted({t.name for ts in AGENT_TOOLS.values() for t in ts}, key=len, reverse=True)


def _looks_like_tool_json(text: str) -> bool:
    t = text.strip().strip("`").strip()
    if t.lower().startswith("json"):
        t = t[4:].strip()
    return t.startswith("{") and ("transfer_to_" in t or any(f'"{n}"' in t for n in _ALL_TOOL_NAMES))


def _text_tool_target(text: str, current: str) -> str | None:
    """Agent a text-written tool call was meant for (handoff target, or owner of another agent's tool)."""
    if not _looks_like_tool_json(text):
        return None
    m = re.search(r"transfer_to_(triage|storage|process|dependency)", text)
    if m and m.group(1) != current:
        return m.group(1)
    for n in _ALL_TOOL_NAMES:
        if f'"{n}"' in text:
            return _owner_of(n, exclude=current)
    return None


def _failed_tool(error_text: str) -> str:
    """Tool name from a provider error like "attempted to call tool 'delete_files' which was not in request.tools"."""
    m = re.search(r"call(?:ed)? tool \\?['\"]?([A-Za-z0-9_\-]+)", error_text)
    return m.group(1) if m else ""


def _owner_of(tool: str, exclude: str) -> str | None:
    """Which agent actually has this tool (used to auto-route when the model skips the handoff)."""
    for agent, tools in AGENT_TOOLS.items():
        if agent != exclude and any(t.name == tool for t in tools):
            return agent
    return None


def _tool_list(name: str) -> str:
    """The exact tools this agent may call, so the model never invents another agent's tool."""
    direct = [t.name for t in AGENT_TOOLS[name] if t.name in SHOW_TOOL_NAMES]
    approval = [t.name for t in AGENT_TOOLS[name] if t.name in ACTION_TOOL_NAMES]
    data = [t.name for t in AGENT_TOOLS[name] if t.name not in SHOW_TOOL_NAMES | ACTION_TOOL_NAMES]
    handoffs = [f"transfer_to_{a}" for a in AGENTS if a != name]
    lines = ["YOUR TOOLS (the only tools you can call):"]
    if direct:
        lines.append(f"- DIRECT (answer the user, end your turn): {', '.join(direct)}")
    if data:
        lines.append(f"- DATA (result comes back to you): {', '.join(data)}")
    if approval:
        lines.append(f"- NEED APPROVAL: {', '.join(approval)}")
    lines.append(f"- HANDOFF: {', '.join(handoffs)}")
    return "\n".join(lines)


def _prompt_middleware(name: str):
    base = PROMPTS[name] + "\n" + _tool_list(name)

    @dynamic_prompt
    def prompt(request: ModelRequest) -> str:
        note = (request.state or {}).get("handoff_note") or ""
        return base + (f"\n\nHANDOFF CONTEXT: {note}" if note else "")

    return prompt


def build_graph(model: BaseChatModel, checkpointer=None):
    """Compile the multi-agent graph. Reusing the same checkpointer keeps the
    conversation when the model is switched in Settings."""
    agents = {
        name: create_agent(
            model,
            tools=[*AGENT_TOOLS[name], *handoff_tools_for(name, AGENTS)],
            middleware=[_prompt_middleware(name)],
            state_schema=DevBoxState,
            name=name,
        )
        for name in AGENTS
    }

    def make_node(name: str):
        agent = agents[name]

        def node(state: DevBoxState) -> Command:
            ctx = build_context(state["messages"])
            note = state.get("handoff_note", "")

            def run(handoff_note: str):
                return agent.invoke(
                    {"messages": ctx,
                     "handoff_note": handoff_note,
                     "handoff_count": state.get("handoff_count", 0),
                     "handoff_from": state.get("handoff_from", ""),
                     "active_agent": name},
                    config={"tags": [f"agent:{name}"], "recursion_limit": 40,
                            "run_name": f"{name} agent", "metadata": {"agent": name}},
                )

            try:
                result = run(note)
            except Exception as e:
                # Groq/OpenAI reject calls to tools this agent doesn't have (400 tool_use_failed).
                if "tool_use_failed" not in str(e) and "not in request.tools" not in str(e):
                    raise
                tool = _failed_tool(str(e))
                owner = _owner_of(tool, exclude=name)
                count = int(state.get("handoff_count", 0) or 0)
                if owner and count < config.MAX_HANDOFFS_PER_TURN:
                    # The model skipped the handoff. We know who owns the tool, so the code
                    # does the handoff itself; the owner agent then calls the tool properly.
                    route = AIMessage(content=f"Routed from {name} to {owner} (needs {tool}).",
                                      response_metadata={"devbox_route": True})
                    return Command(goto=owner, update={
                        "messages": [route],
                        "active_agent": owner,
                        "handoff_note": (f"The {name} agent handed this request to you because it "
                                         f"needs your tool '{tool}'. Handle the user's last message."),
                        "handoff_count": count + 1,
                        "handoff_from": name,
                    })
                # Unknown tool: retry once with an explicit correction.
                fix = ("Your previous reply called a tool you do not have. Call ONLY a tool from "
                       "YOUR TOOLS; if the request belongs to another agent, call transfer_to_<agent>.")
                result = run(f"{note}\n{fix}".strip())

            if _is_empty_reply(result["messages"][len(ctx):]):
                # Reasoning models sometimes spend the whole output budget thinking and
                # return nothing. Retry once with a nudge, then fall back to a clear message.
                nudge = ("Your previous reply was empty. Either call ONE tool from YOUR TOOLS, "
                         "call transfer_to_<agent>, or answer in one or two short sentences.")
                result = run(f"{note}\n{nudge}".strip())
                if _is_empty_reply(result["messages"][len(ctx):]):
                    result["messages"] = result["messages"][:-1] + [AIMessage(
                        content="I couldn't produce an answer for that. Please rephrase, or pick a "
                                "larger model in Settings → Model.")]
            new_messages = result["messages"][len(ctx):]

            # Some models write the tool call as text, e.g. {"transfer_to_storage": {...}}.
            # Treat it as the handoff it was meant to be instead of showing JSON to the user.
            last = new_messages[-1] if new_messages else None
            target = (_text_tool_target(text_of(last), name)
                      if isinstance(last, AIMessage) and not last.tool_calls else None)
            count = int(state.get("handoff_count", 0) or 0)
            if target and count < config.MAX_HANDOFFS_PER_TURN and state.get("handoff_from") != target:
                route = AIMessage(content=f"Routed from {name} to {target}.",
                                  response_metadata={"devbox_route": True})
                return Command(goto=target, update={
                    "messages": [*new_messages[:-1], route],
                    "active_agent": target,
                    "handoff_note": f"The {name} agent handed this request to you. Handle the user's last message.",
                    "handoff_count": count + 1,
                    "handoff_from": name,
                })
            return Command(goto=END, update={"messages": new_messages, "active_agent": name,
                                             "handoff_note": ""})

        return node

    builder = StateGraph(DevBoxState)
    for name in AGENTS:
        builder.add_node(name, make_node(name), destinations=tuple(a for a in AGENTS if a != name) + (END,))
    builder.add_conditional_edges(START, lambda s: s.get("active_agent") or "triage", AGENTS)
    return builder.compile(checkpointer=checkpointer or InMemorySaver())
