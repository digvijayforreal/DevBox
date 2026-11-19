"""What an agent is: a name, a role prompt, and its tools grouped by type."""

from __future__ import annotations

from dataclasses import dataclass, field

from langchain_core.tools import BaseTool


@dataclass(frozen=True)
class AgentSpec:
    name: str
    description: str  # used in the transfer_to_<name> tool that other agents see
    prompt: str       # role instructions (the shared rules in common.py are added in front)
    data_tools: list[BaseTool] = field(default_factory=list)    # NORMAL: result goes back to the LLM
    direct_tools: list[BaseTool] = field(default_factory=list)  # NORMAL: card to the user (agents/direct.py)
    risk_tools: list[BaseTool] = field(default_factory=list)    # RISK: pause for the user's approval

    @property
    def tools(self) -> list[BaseTool]:
        return [*self.data_tools, *self.direct_tools, *self.risk_tools]

    def __post_init__(self) -> None:
        # The built-in flag would end the turn before our custom rule (agents/direct.py) runs.
        for t in self.tools:
            assert not t.return_direct, (f"{self.name}: {t.name} has return_direct=True; remove it, "
                                         "agents/direct.py decides when a tool ends the turn")
