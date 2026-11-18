from devbox.tools.actions import ACTION_TOOL_NAMES, ACTION_TOOLS
from devbox.tools.normal import DIRECT_LOOKUP_TOOL_NAMES, NORMAL_TOOLS
from devbox.tools.show import SHOW_TOOLS
from devbox.tools.show import SHOW_TOOL_NAMES as _SHOW_ONLY

# Every tool with return_direct=True: its output goes straight to the user and ends
# the turn. The UI renders these as report cards; the activity log tags them "show & stop".
SHOW_TOOL_NAMES = _SHOW_ONLY | DIRECT_LOOKUP_TOOL_NAMES

__all__ = ["NORMAL_TOOLS", "SHOW_TOOLS", "ACTION_TOOLS", "SHOW_TOOL_NAMES", "ACTION_TOOL_NAMES"]
