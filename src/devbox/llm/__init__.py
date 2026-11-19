"""LLM layer: providers.py (Groq / OpenAI / NVIDIA, selected model) and usage.py (rate limits).

The provider functions are re-exported, so `from devbox import llm; llm.current()` keeps working.
"""

from devbox.llm.providers import (
    PROVIDERS,
    Selection,
    api_key,
    current,
    env_model,
    list_models,
    make_model,
    resolve,
    set_selection,
)

__all__ = ["PROVIDERS", "Selection", "api_key", "current", "env_model", "list_models", "make_model",
           "resolve", "set_selection"]
