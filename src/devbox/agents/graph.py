"""Builds the multi-agent graph.

    START ──route by active_agent──► triage | storage | process | dependency
       each agent node = create_agent(...) with its own tools + transfer_to_* tools
       handoff  : tool returns Command(graph=PARENT, goto=<agent>)   -> jump to agent
       type A   : show_* tool has return_direct=True                -> agent stops, table to user
       type B   : action tool calls interrupt(preview)              -> graph pauses for approval
       finished : node returns Command(goto=END)
"""

from __future__ import annotations

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
    "process": [N.list_running_processes, N.check_port_in_use, N.get_system_info,
                S.show_system_info, S.show_process_table, A.kill_process],
    "dependency": [N.scan_outdated_packages, N.search_installed_programs,
                   S.show_vulnerable_dependencies, S.show_git_repos_with_uncommitted_changes,
                   A.update_package, A.uninstall_program],
}


class DevBoxState(AgentState):
    active_agent: NotRequired[str]
    handoff_note: NotRequired[str]
    handoff_count: NotRequired[int]


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


def build_context(messages: list, max_messages: int = 30) -> list:
    """Context engineering for handoffs.

    Each agent gets a clean history: user messages, final AI answers, and the
    exact tables already shown to the user. Tool-call chatter from other agents
    (and handoff pairs) is dropped so the LLM never sees calls to tools it doesn't own.
    """
    out: list = []

    def add_ai(text: str) -> None:
        if out and isinstance(out[-1], AIMessage):
            out[-1] = AIMessage(content=text_of(out[-1]) + "\n\n" + text)
        else:
            out.append(AIMessage(content=text))

    for m in messages:
        if isinstance(m, HumanMessage):
            out.append(HumanMessage(content=text_of(m)))
        elif isinstance(m, AIMessage):
            if m.tool_calls:
                continue
            t = text_of(m).strip()
            if t:
                add_ai(t)
        elif isinstance(m, ToolMessage):
            if m.name in SHOW_TOOL_NAMES:
                add_ai(f"[Table shown to the user by {m.name}]\n{truncate(text_of(m), 3000)}")
            elif m.name in ACTION_TOOL_NAMES:
                add_ai(f"[{m.name} result] {truncate(text_of(m), 600)}")
    out = out[-max_messages:]
    while out and not isinstance(out[0], HumanMessage):
        out.pop(0)
    return out


def _prompt_middleware(name: str):
    base = PROMPTS[name]

    @dynamic_prompt
    def prompt(request: ModelRequest) -> str:
        note = (request.state or {}).get("handoff_note") or ""
        return base + (f"\n\nHANDOFF CONTEXT: {note}" if note else "")

    return prompt


def default_model() -> BaseChatModel:
    import httpx
    from langchain_groq import ChatGroq

    from devbox.usage import usage

    if not config.GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is missing. Add it to .env (get one at https://console.groq.com/keys) and restart.")

    def capture_rate_limits(response: httpx.Response) -> None:
        usage.record_headers(response.headers)  # real RPD / TPM remaining from Groq

    http_client = httpx.Client(timeout=120, event_hooks={"response": [capture_rate_limits]})
    return ChatGroq(model=config.GROQ_MODEL, api_key=config.GROQ_API_KEY, temperature=0.2,
                    max_retries=2, http_client=http_client)


def build_graph(model: BaseChatModel | None = None, checkpointer=None):
    model = model or default_model()
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
            result = agent.invoke(
                {"messages": ctx,
                 "handoff_note": state.get("handoff_note", ""),
                 "handoff_count": state.get("handoff_count", 0),
                 "active_agent": name},
                config={"tags": [f"agent:{name}"], "recursion_limit": 40},
            )
            new_messages = result["messages"][len(ctx):]
            return Command(goto=END, update={"messages": new_messages, "active_agent": name,
                                             "handoff_note": ""})

        return node

    builder = StateGraph(DevBoxState)
    for name in AGENTS:
        builder.add_node(name, make_node(name), destinations=tuple(a for a in AGENTS if a != name) + (END,))
    builder.add_conditional_edges(START, lambda s: s.get("active_agent") or "triage", AGENTS)
    return builder.compile(checkpointer=checkpointer or InMemorySaver())
