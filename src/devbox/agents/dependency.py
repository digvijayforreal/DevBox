"""Dependency agent: packages, vulnerabilities, git repos, installed programs."""

from devbox.agents.base import AgentSpec
from devbox.prompts.dependency import PROMPT
from devbox.tools.dependency.scan_outdated_packages import scan_outdated_packages
from devbox.tools.dependency.search_installed_programs import search_installed_programs
from devbox.tools.dependency.show_git_repos_with_uncommitted_changes import show_git_repos_with_uncommitted_changes
from devbox.tools.dependency.show_vulnerable_dependencies import show_vulnerable_dependencies
from devbox.tools.dependency.uninstall_program import uninstall_program
from devbox.tools.dependency.update_package import update_package

AGENT = AgentSpec(
    name="dependency",
    description="the DEPENDENCY agent (outdated packages, vulnerabilities, git repos, updates, uninstalling programs)",
    prompt=PROMPT,
    data_tools=[search_installed_programs],
    direct_tools=[scan_outdated_packages, show_vulnerable_dependencies, show_git_repos_with_uncommitted_changes],
    risk_tools=[update_package, uninstall_program],
)
