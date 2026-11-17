"""LLM providers (Groq + OpenAI), the selected model, and model listing.

The selection (provider + model) is changed from Settings → Model and saved to
.devbox/model.json so it survives restarts. API keys only ever come from .env.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass

import httpx
from langchain_core.language_models import BaseChatModel

from devbox import config
from devbox.usage import tracker

PROVIDERS = {
    "groq": {"label": "Groq", "base_url": "https://api.groq.com/openai/v1",
             "keys_url": "https://console.groq.com/keys", "env_key": "GROQ_API_KEY"},
    "openai": {"label": "OpenAI", "base_url": "https://api.openai.com/v1",
               "keys_url": "https://platform.openai.com/api-keys", "env_key": "OPENAI_API_KEY"},
}

# Models that can't be used for a tool-calling chat agent.
_SKIP = ("whisper", "tts", "orpheus", "guard", "safeguard", "embedding", "embed", "audio", "realtime",
         "transcribe", "image", "dall-e", "moderation", "search", "sora", "davinci", "babbage", "instruct")


def api_key(provider: str) -> str:
    return config.GROQ_API_KEY if provider == "groq" else config.OPENAI_API_KEY


def env_model(provider: str) -> str:
    return config.GROQ_MODEL if provider == "groq" else config.OPENAI_MODEL


@dataclass
class Selection:
    provider: str
    model: str


_lock = threading.Lock()
_model_cache: dict[str, tuple[float, list[str]]] = {}


def list_models(provider: str, refresh: bool = False) -> list[str]:
    """Chat-capable model IDs available to this key (GET /models, cached 10 min)."""
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider '{provider}'")
    key = api_key(provider)
    if not key:
        raise RuntimeError(f"{PROVIDERS[provider]['env_key']} is not set in .env")
    cached = _model_cache.get(provider)
    if cached and not refresh and time.time() - cached[0] < 600:
        return cached[1]
    r = httpx.get(f"{PROVIDERS[provider]['base_url']}/models",
                  headers={"Authorization": f"Bearer {key}"}, timeout=20)
    r.raise_for_status()
    ids = [m.get("id", "") for m in r.json().get("data", [])]
    chat = sorted({i for i in ids if i and not any(s in i.lower() for s in _SKIP)})
    if provider == "openai":
        chat = [i for i in chat if i.startswith(("gpt-", "o1", "o3", "o4", "o5", "chatgpt-"))] or chat
    _model_cache[provider] = (time.time(), chat)
    return chat


def _default_selection() -> Selection:
    pref = config.LLM_PROVIDER
    order = [pref] if pref in PROVIDERS else []
    order += [p for p in ("groq", "openai") if p not in order]
    for p in order:
        if api_key(p):
            return Selection(p, env_model(p))
    return Selection(order[0], env_model(order[0]))


def _load() -> Selection:
    try:
        data = json.loads(config.SELECTION_FILE.read_text(encoding="utf-8"))
        if data.get("provider") in PROVIDERS:
            return Selection(data["provider"], str(data.get("model") or ""))
    except (OSError, ValueError):
        pass
    return _default_selection()


_selection = _load()


def current() -> Selection:
    with _lock:
        return Selection(_selection.provider, _selection.model)


def set_selection(provider: str, model: str) -> Selection:
    global _selection
    provider, model = provider.strip().lower(), model.strip()
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider '{provider}'.")
    if not api_key(provider):
        raise ValueError(f"{PROVIDERS[provider]['env_key']} is missing in .env. Add it and restart DevBox.")
    if not model or len(model) > 120 or any(c.isspace() for c in model):
        raise ValueError("Pick a valid model name.")
    with _lock:
        _selection = Selection(provider, model)
        try:
            config.SELECTION_FILE.write_text(json.dumps(asdict(_selection)), encoding="utf-8")
        except OSError:
            pass
        return Selection(provider, model)


def resolve(sel: Selection) -> Selection:
    """Fill in a model if none is set (first chat model on the account)."""
    if sel.model:
        return sel
    try:
        models = list_models(sel.provider)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"No model selected for {PROVIDERS[sel.provider]['label']} and the model list "
                           f"could not be loaded ({e}). Pick one in Settings → Model.") from e
    if not models:
        raise RuntimeError("No chat models available for this key. Pick one in Settings → Model.")
    return Selection(sel.provider, models[0])


def make_model(sel: Selection) -> BaseChatModel:
    key = api_key(sel.provider)
    if not key:
        info = PROVIDERS[sel.provider]
        raise RuntimeError(f"{info['env_key']} is missing. Add it to .env (get one at {info['keys_url']}) "
                           "and restart, or switch provider in Settings → Model.")
    usage = tracker(sel.provider)

    def capture_rate_limits(response: httpx.Response) -> None:
        usage.record_headers(response.headers)  # real limits/remaining from the provider

    http_client = httpx.Client(timeout=120, event_hooks={"response": [capture_rate_limits]})
    if sel.provider == "groq":
        from langchain_groq import ChatGroq

        return ChatGroq(model=sel.model, api_key=key, temperature=0.2, max_retries=2, http_client=http_client)

    from langchain_openai import ChatOpenAI

    # No temperature: some OpenAI reasoning models reject it.
    return ChatOpenAI(model=sel.model, api_key=key, max_retries=2, http_client=http_client)
