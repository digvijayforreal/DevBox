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
    direct_tools: list[BaseTool] = field(default_factory=list)  # NORMAL: return_direct, card to the user
    risk_tools: list[BaseTool] = field(default_factory=list)    # RISK: pause for the user's approval

    @property
    def tools(self) -> list[BaseTool]:
        return [*self.data_tools, *self.direct_tools, *self.risk_tools]

    def __post_init__(self) -> None:
        # Keep the folder honest: direct tools must really be return_direct, the others must not.
        for t in self.direct_tools:
            assert t.return_direct, f"{self.name}: {t.name} is listed as direct but has no return_direct=True"
        for t in (*self.data_tools, *self.risk_tools):
            assert not t.return_direct, f"{self.name}: {t.name} has return_direct=True but is not listed as direct"
