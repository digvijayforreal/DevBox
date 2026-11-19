"""General agent: greetings and small talk, chosen by CODE (not by the LLM) to save tokens.

    "hi" / "thanks" / "what can you do"  -> is_small_talk() is True -> general node
                                            (one tiny LLM call: short prompt, no tools, no history)
    anything with a real request          -> normal routing (active agent or triage)

It never changes active_agent, so after "thanks" you stay with the current specialist.
"""

from __future__ import annotations

import re

from langchain.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.language_models import BaseChatModel
from langgraph.graph import END
from langgraph.types import Command

from devbox.prompts.general import PROMPT

NAME = "general"

_WORDS = {
    "hi", "hii", "hiii", "hello", "hey", "heya", "yo", "sup", "hola", "namaste",
    "thanks", "thank", "thankyou", "thx", "ty", "you", "so", "much", "a", "lot",
    "ok", "okay", "k", "cool", "nice", "great", "awesome", "good", "fine",
    "morning", "afternoon", "evening", "night", "bye", "goodbye", "see", "ya",
    "there", "devbox", "buddy", "bro",
}
_PHRASES = {"what can you do", "who are you", "what are you", "help", "how are you"}
_FALLBACK = "Hi! I can check your disk space, big files, running programs, ports, packages and system details."


def is_small_talk(text: str) -> bool:
    """True only for short, pure greetings / thanks / 'what can you do'."""
    t = re.sub(r"[^a-z ]", " ", text.lower()).split()
    if not t or len(t) > 6:
        return False
    return " ".join(t) in _PHRASES or all(w in _WORDS for w in t)


def last_user_text(state: dict) -> str:
    for m in reversed(state.get("messages", [])):
        if isinstance(m, HumanMessage):
            return m.content if isinstance(m.content, str) else ""
    return ""


def make_general_node(model: BaseChatModel):
    def node(state: dict) -> Command:
        text = last_user_text(state)
        reply = model.invoke([SystemMessage(content=PROMPT), HumanMessage(content=text)],
                             config={"tags": [f"agent:{NAME}"], "run_name": "general agent"})
        content = reply.content if isinstance(reply.content, str) else ""
        return Command(goto=END, update={"messages": [AIMessage(content=content.strip() or _FALLBACK,
                                                                name=NAME)]})

    return node
