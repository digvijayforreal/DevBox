r"""Demo: a custom "return direct" (no `return_direct=True` on any tool).

Run:  uv run python demos/custom_return_direct.py

The agent loop is built by hand with LangGraph:

    START -> llm -> (tool call?) -> tools -> should_end()? -> END   (tool answers the user)
                                                        \-> llm    (LLM reads the result)

`should_end()` is OUR rule. The built-in flag can only say "always" or "never";
here the decision looks at the tool name, the tool's result and the user's message.
A scripted fake model is used, so this runs offline and costs no tokens.
"""

from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages


# --------------------------------------------------------------------------- tools
# Plain tools: NO return_direct=True anywhere.
@tool
def get_disk_usage() -> str:
    """Show used/free space for every drive."""
    return "C:\\ 138 GB free of 237 GB (42% used)"


@tool
def find_large_files(folder: str) -> str:
    """Find large files in a folder."""
    if "Windows" in folder:
        return "Error: 'C:\\Windows' is a protected location."
    return "240 MB VSCodeUserSetup.exe\n188 MB mongodb-compass.exe"


@tool
def check_port_in_use(port: int) -> str:
    """Check which process uses a port."""
    return "Port 8080 is free." if port == 8080 else "python.exe (PID 4242) is listening on 3000"


TOOLS = {t.name: t for t in (get_disk_usage, find_large_files, check_port_in_use)}


# --------------------------------------------------------------------------- OUR rule
DIRECT_TOOLS = {"get_disk_usage", "find_large_files"}   # normally answer the user directly


def should_end(tool_name: str, result: str, user_text: str) -> tuple[bool, str]:
    """Decide after EVERY tool call: end the turn with the tool's output, or go back to the LLM."""
    if result.startswith("Error"):
        return False, "error -> let the LLM explain it"
    if any(w in user_text.lower() for w in ("if not", "then", "and free", "free it")):
        return False, "user asked for a follow-up step -> keep going"
    if tool_name in DIRECT_TOOLS:
        return True, "direct tool -> output goes straight to the user"
    if tool_name == "check_port_in_use" and result.endswith("is free."):
        return True, "simple answer -> no need for an extra LLM call"
    return False, "LLM needs the data"


# --------------------------------------------------------------------------- graph
class State(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]


def build(model):
    def llm(state: State):
        print("   🧠 LLM call")
        return {"messages": [model.invoke(state["messages"])]}

    def tools(state: State):
        call = state["messages"][-1].tool_calls[0]
        result = TOOLS[call["name"]].invoke(call["args"])
        print(f"   🔧 {call['name']}({call['args']}) -> {result.splitlines()[0]!r}")
        return {"messages": [ToolMessage(content=result, name=call["name"], tool_call_id=call["id"])]}

    def after_llm(state: State):
        return "tools" if state["messages"][-1].tool_calls else END

    def after_tools(state: State):  # <-- the custom "return direct"
        msgs = state["messages"]
        user_text = next(m.content for m in reversed(msgs) if isinstance(m, HumanMessage))
        end, why = should_end(msgs[-1].name, msgs[-1].content, user_text)
        print(f"   {'🛑 END   ' if end else '↩️  back to LLM'}  ({why})")
        return END if end else "llm"

    g = StateGraph(State)
    g.add_node("llm", llm)
    g.add_node("tools", tools)
    g.add_edge(START, "llm")
    g.add_conditional_edges("llm", after_llm, ["tools", END])
    g.add_conditional_edges("tools", after_tools, ["llm", END])
    return g.compile()


# --------------------------------------------------------------------------- demo
class ScriptedModel(GenericFakeChatModel):
    """Stands in for a real LLM: returns the next scripted reply on each call."""


def call(name, args):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": name}])


CASES = [
    ("How full are my drives?", [call("get_disk_usage", {})]),
    ("Find big files in C:\\Windows", [call("find_large_files", {"folder": "C:\\Windows"}),
                                        AIMessage(content="That folder is protected, I can't scan it.")]),
    ("Is port 8080 free?", [call("check_port_in_use", {"port": 8080})]),
    ("Is port 3000 free? If not, free it up", [call("check_port_in_use", {"port": 3000}),
                                                AIMessage(content="(next it would call kill_process)")]),
]

if __name__ == "__main__":
    for question, script in CASES:
        print(f"\n👤 {question}")
        graph = build(ScriptedModel(messages=iter(script)))
        final = graph.invoke({"messages": [HumanMessage(content=question)]})["messages"][-1]
        who = "TOOL" if isinstance(final, ToolMessage) else "LLM"
        print(f"   ➜ answer from {who}: {final.content.splitlines()[0]!r}")
