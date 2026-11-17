"""Local web server for the DevBox frontend.

* FastAPI + one WebSocket (/ws) for chat, live activity events and approvals.
* Binds to 127.0.0.1 only. Because this app can delete files and kill processes,
  every request also needs a random per-launch token (in the URL that DevBox opens),
  and Host/Origin headers must be localhost (blocks DNS-rebinding and other websites
  talking to the socket).
* The agent runs in a worker thread. When a TYPE B tool interrupts, the thread
  blocks on a Future until the browser answers the approval dialog.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import threading
import uuid
import webbrowser
from collections import deque
from concurrent.futures import Future
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from devbox import config, llm
from devbox.system_report import build_system_report
from devbox.usage import tracker

STATIC_DIR = Path(__file__).parent / "static"
TOKEN = secrets.token_urlsafe(24)


def allowed_hosts() -> set[str]:
    return {f"127.0.0.1:{config.WEB_PORT}", f"localhost:{config.WEB_PORT}"}


def allowed_origins() -> set[str]:
    return {f"http://{h}" for h in allowed_hosts()}


# ---------------------------------------------------------------------------
class Hub:
    """Shared state between the agent thread and connected browser tabs."""

    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.clients: dict[WebSocket, asyncio.Queue] = {}
        self.pending: dict[str, tuple[dict, Future]] = {}
        self.transcript: list[dict] = []
        self.activity: deque[dict] = deque(maxlen=500)
        self.busy = False
        self.agent = "triage"
        self.session = None
        self.lock = threading.Lock()

    # -- fan-out (thread-safe) ---------------------------------------------
    def _fanout(self, msg: dict) -> None:
        for q in list(self.clients.values()):
            q.put_nowait(msg)

    def send_all(self, msg: dict) -> None:
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self._fanout, msg)

    def add_item(self, item: dict) -> None:
        self.transcript.append(item)
        self.send_all({"type": "item", "item": item})

    def sink(self, event: dict) -> None:
        """Callback handler -> activity panel."""
        self.activity.append(event)
        self.send_all({"type": "event", "event": event})

    # -- approvals (called from the agent thread) --------------------------
    def ask(self, payload: dict) -> dict:
        aid = uuid.uuid4().hex[:12]
        fut: Future = Future()
        self.pending[aid] = (payload, fut)
        self.send_all({"type": "approval_request", "id": aid, "payload": payload})
        try:
            return fut.result()
        finally:
            self.pending.pop(aid, None)
            self.send_all({"type": "approval_closed", "id": aid})

    def resolve(self, aid: str, decision: dict) -> None:
        entry = self.pending.get(aid)
        if entry and not entry[1].done():
            entry[1].set_result(decision)

    # -- model selection -------------------------------------------------------
    def set_model(self, provider: str, model: str) -> str | None:
        with self.lock:
            if self.busy:
                return "Wait for the current request to finish before switching models."
            try:
                sel = llm.set_selection(provider, model)
            except ValueError as e:
                return str(e)
        # The Session rebuilds its graph on the next turn and keeps the conversation.
        self.send_all({"type": "model", "value": llm_state()})
        self.add_item({"type": "note", "text": f"Model switched to {llm.PROVIDERS[sel.provider]['label']} · {sel.model}"})
        return None

    # -- turns ---------------------------------------------------------------
    def start_turn(self, text: str) -> str | None:
        with self.lock:
            if self.busy:
                return "DevBox is still working on the previous request."
            self.busy = True
        self.send_all({"type": "busy", "value": True})
        self.add_item({"type": "user", "text": text})
        threading.Thread(target=self._run_turn, args=(text,), daemon=True).start()
        return None

    def _run_turn(self, text: str) -> None:
        try:
            if self.session is None:
                from devbox.engine import Session
                self.session = Session(sink=self.sink)
            outputs = self.session.run(text, self.ask)
            for o in outputs:
                self.add_item(o)
            if not outputs:
                self.add_item({"type": "ai", "text": "_(no reply)_"})
            self.agent = self.session.active_agent
            self.send_all({"type": "agent", "value": self.agent})
        except Exception as e:  # report every failure in the chat
            self.add_item({"type": "error", "text": f"{type(e).__name__}: {e}"})
        finally:
            with self.lock:
                self.busy = False
            self.send_all({"type": "busy", "value": False})

    def reset(self) -> str | None:
        with self.lock:
            if self.busy:
                return "Wait for the current request to finish first."
            if self.session is not None:
                self.session.reset()
            self.transcript.clear()
            self.agent = "triage"
        self.send_all({"type": "reset"})
        return None

    def hello(self) -> dict:
        return {
            "type": "hello",
            "transcript": list(self.transcript),
            "activity": list(self.activity),
            "busy": self.busy,
            "agent": self.agent,
            "pending": [{"id": k, "payload": v[0]} for k, v in self.pending.items()],
            "model": llm_state(),
        }


def llm_state() -> dict:
    sel = llm.current()
    return {
        "provider": sel.provider,
        "model": sel.model,
        "providers": {name: {"label": p["label"], "key_set": bool(llm.api_key(name)),
                             "env_key": p["env_key"], "keys_url": p["keys_url"],
                             "default_model": llm.env_model(name)}
                      for name, p in llm.PROVIDERS.items()},
    }


hub = Hub()


def _clean_decision(raw: Any) -> dict:
    d = raw if isinstance(raw, dict) else {}
    decision = d.get("decision")
    out: dict = {"decision": decision if decision in ("approve", "edit", "reject") else "reject"}
    if out["decision"] == "edit":
        out["args"] = d.get("args") if isinstance(d.get("args"), dict) else {}
    if out["decision"] == "reject":
        out["reason"] = str(d.get("reason") or "user said no")[:300]
    return out


# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(_: FastAPI):
    hub.loop = asyncio.get_running_loop()
    yield


app = FastAPI(title="DevBox", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def host_guard(request: Request, call_next):
    if request.headers.get("host") not in allowed_hosts():
        return PlainTextResponse("Forbidden host", status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; connect-src 'self' ws://127.0.0.1:* ws://localhost:*; "
        "img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'")
    return response


def require_token(x_devbox_token: str = Header(default="")) -> None:
    if not secrets.compare_digest(x_devbox_token, TOKEN):
        raise HTTPException(status_code=401, detail="Invalid DevBox token")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})


# Fully transparent tab icon. Also served at /favicon.ico so browsers drop any
# icon they cached from another app that used this port before.
_BLANK_ICON = '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"/>'


@app.get("/favicon.svg")
@app.get("/favicon.ico")
def favicon() -> Response:
    return Response(_BLANK_ICON, media_type="image/svg+xml", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/api/usage", dependencies=[Depends(require_token)])
def api_usage(provider: str = "") -> dict:
    sel = llm.current()
    name = provider if provider in llm.PROVIDERS else sel.provider
    snap = tracker(name).snapshot()
    snap["model"] = sel.model if name == sel.provider else llm.env_model(name)
    snap["key_set"] = bool(llm.api_key(name))
    snap["active"] = name == sel.provider
    return snap


@app.get("/api/llm", dependencies=[Depends(require_token)])
def api_llm() -> dict:
    return llm_state()


@app.get("/api/models", dependencies=[Depends(require_token)])
def api_models(provider: str, refresh: bool = False) -> dict:
    if provider not in llm.PROVIDERS:
        raise HTTPException(status_code=400, detail="Unknown provider")
    try:
        return {"provider": provider, "models": llm.list_models(provider, refresh=refresh), "error": None}
    except Exception as e:  # missing key, network, bad key: show it in the UI
        msg = str(e)
        if "401" in msg:
            msg = "The API key was rejected (401). Check it in .env."
        return {"provider": provider, "models": [], "error": msg[:300]}


@app.get("/api/system", dependencies=[Depends(require_token)])
def api_system() -> dict:
    return json.loads(build_system_report())


@app.get("/api/config", dependencies=[Depends(require_token)])
def api_config() -> dict:
    from devbox.agents.graph import AGENT_TOOLS, AGENTS
    from devbox.callbacks import tool_kind

    return {
        "keys": {"groq": bool(config.GROQ_API_KEY), "openai": bool(config.OPENAI_API_KEY)},
        "limits": {
            "groq": {"rpm": config.GROQ_RPM_LIMIT, "rpd": config.GROQ_RPD_LIMIT,
                     "tpm": config.GROQ_TPM_LIMIT, "tpd": config.GROQ_TPD_LIMIT},
            "openai": {"rpm": config.OPENAI_RPM_LIMIT, "tpm": config.OPENAI_TPM_LIMIT,
                       "rpd": config.OPENAI_RPD_LIMIT},
        },
        "allowed_roots": [str(p) for p in config.ALLOWED_ROOTS],
        "protected_paths": [str(p) for p in config.PROTECTED_PATHS],
        "port": config.WEB_PORT,
        "logs": {"activity": str(config.ACTIVITY_LOG), "actions": str(config.ACTION_LOG),
                 "state": str(config.STATE_DIR)},
        "agents": {a: [{"name": t.name, "kind": tool_kind(t.name)} for t in AGENT_TOOLS[a]]
                   + [{"name": f"transfer_to_{b}", "kind": "handoff"} for b in AGENTS if b != a]
                   for a in AGENTS},
    }


@app.get("/api/actions", dependencies=[Depends(require_token)])
def api_actions() -> dict:
    try:
        lines = config.ACTION_LOG.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    return {"lines": lines[-100:][::-1]}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    ok = (ws.headers.get("host") in allowed_hosts()
          and ws.headers.get("origin") in allowed_origins()
          and secrets.compare_digest(ws.query_params.get("token", ""), TOKEN))
    await ws.accept()
    if not ok:
        await ws.close(code=4401)
        return

    queue: asyncio.Queue = asyncio.Queue()
    queue.put_nowait(hub.hello())  # same tick as registering -> no missed/duplicated messages
    hub.clients[ws] = queue

    async def sender() -> None:
        while True:
            await ws.send_json(await queue.get())

    task = asyncio.create_task(sender())
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            kind = msg.get("type")
            if kind == "chat":
                text = str(msg.get("text", "")).strip()[:4000]
                if text:
                    err = hub.start_turn(text)
                    if err:
                        queue.put_nowait({"type": "toast", "level": "warn", "text": err})
            elif kind == "approval":
                hub.resolve(str(msg.get("id", "")), _clean_decision(msg.get("decision")))
            elif kind == "reset":
                err = hub.reset()
                if err:
                    queue.put_nowait({"type": "toast", "level": "warn", "text": err})
            elif kind == "set_model":
                err = hub.set_model(str(msg.get("provider", "")), str(msg.get("model", "")))
                queue.put_nowait({"type": "toast", "level": "warn" if err else "info",
                                  "text": err or "Model updated. The next message uses it."})
    except WebSocketDisconnect:
        pass
    finally:
        hub.clients.pop(ws, None)
        task.cancel()


# ---------------------------------------------------------------------------
def main() -> None:
    url = f"http://127.0.0.1:{config.WEB_PORT}/?token={TOKEN}"
    print("\n  DevBox is running (local only).")
    print(f"  Open: {url}\n  Press Ctrl+C to stop.\n")
    if config.OPEN_BROWSER:
        threading.Timer(1.2, webbrowser.open, args=(url,)).start()
    uvicorn.run(app, host="127.0.0.1", port=config.WEB_PORT, log_level="warning")
