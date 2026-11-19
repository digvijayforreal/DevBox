"""Storage agent: disk space, large files, node_modules, caches, cleanup."""

from devbox.agents.base import AgentSpec
from devbox.prompts.storage import PROMPT
from devbox.tools.dependency.show_git_repos_with_uncommitted_changes import (  # shared with dependency
    show_git_repos_with_uncommitted_changes,
)
from devbox.tools.storage.clear_cache import clear_cache
from devbox.tools.storage.delete_files import delete_files
from devbox.tools.storage.find_large_files import find_large_files
from devbox.tools.storage.find_node_modules import find_node_modules
from devbox.tools.storage.get_disk_usage import get_disk_usage
from devbox.tools.storage.show_storage_report import show_storage_report

AGENT = AgentSpec(
    name="storage",
    description="the STORAGE agent (disk space, large files, node_modules, caches, deleting files)",
    prompt=PROMPT,
    direct_tools=[get_disk_usage, find_large_files, find_node_modules, show_storage_report,
                  show_git_repos_with_uncommitted_changes],
    risk_tools=[delete_files, clear_cache],
)
