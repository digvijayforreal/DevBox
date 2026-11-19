"""Small helpers shared by several tools."""

SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "__pycache__", ".cache", "$recycle.bin"}


def err(e: Exception) -> str:
    return f"Error: {e}"
