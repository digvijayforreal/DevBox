"""Per-provider usage + rate-limit tracking.

Both providers send real rate-limit state in response headers (captured by an
httpx response hook, see llm.py), but the headers mean different windows:

  header                          Groq                 OpenAI
  x-ratelimit-*-requests          requests per DAY     requests per MINUTE
  x-ratelimit-*-tokens            tokens per MINUTE    tokens per MINUTE
  retry-after (on 429)            seconds              seconds

Anything not in the headers (Groq RPM, Groq tokens/day, OpenAI requests/day) is
counted locally from DevBox's own calls. Limits apply to the whole Groq / OpenAI
organization, so other apps using the same account aren't counted locally.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import deque
from datetime import date

from devbox.core import config

_DUR = re.compile(r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m(?!s))?(?:(\d+(?:\.\d+)?)s)?(?:(\d+(?:\.\d+)?)ms)?")


def parse_duration(text: str | None) -> float | None:
    """'2m59.56s' -> 179.56, '7.66s' -> 7.66, '120ms' -> 0.12, '6m0s' -> 360."""
    if not text:
        return None
    text = text.strip()
    try:
        return float(text)
    except ValueError:
        pass
    m = _DUR.fullmatch(text)
    if not m or not any(m.groups()):
        return None
    h, mi, s, ms = (float(g) if g else 0.0 for g in m.groups())
    return h * 3600 + mi * 60 + s + ms / 1000


def _fmt_secs(secs: float | None) -> str:
    if secs is None:
        return "?"
    secs = max(0, int(secs))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m" if h else f"{m}m {s}s" if m else f"{s}s"


PROFILES = {
    "groq": {
        "label": "Groq", "requests_window": "day",
        "rpm": lambda: config.GROQ_RPM_LIMIT, "rpd": lambda: config.GROQ_RPD_LIMIT,
        "tpm": lambda: config.GROQ_TPM_LIMIT, "tpd": lambda: config.GROQ_TPD_LIMIT,
        "plan": lambda: config.GROQ_PLAN, "limits_url": "https://console.groq.com/settings/limits",
    },
    "openai": {
        "label": "OpenAI", "requests_window": "minute",
        "rpm": lambda: config.OPENAI_RPM_LIMIT, "rpd": lambda: config.OPENAI_RPD_LIMIT,
        "tpm": lambda: config.OPENAI_TPM_LIMIT, "tpd": lambda: 0,
        "plan": lambda: config.OPENAI_TIER,
        "limits_url": "https://platform.openai.com/settings/organization/limits",
    },
    # NVIDIA doesn't document rate-limit headers or token limits for the free tier;
    # requests/minute is counted locally (header meters are used if they ever appear).
    "nvidia": {
        "label": "NVIDIA", "requests_window": "minute", "local_rpm": True,
        "rpm": lambda: config.NVIDIA_RPM_LIMIT, "rpd": lambda: 0,
        "tpm": lambda: 0, "tpd": lambda: 0,
        "plan": lambda: config.NVIDIA_PLAN, "limits_url": "https://build.nvidia.com",
    },
}


class UsageTracker:
    def __init__(self, provider: str) -> None:
        self.provider = provider
        self.p = PROFILES[provider]
        self.lock = threading.Lock()
        self.req_times: deque[float] = deque()
        self.session = {"requests": 0, "input_tokens": 0, "output_tokens": 0,
                        "total_tokens": 0, "errors_429": 0, "errors_other": 0, "throttle_waits": 0}
        self.latencies: deque[float] = deque(maxlen=50)
        self.last_error = ""
        self.h: dict[str, float | None] = {k: None for k in (
            "req_limit", "req_remaining", "req_reset_at", "tok_limit", "tok_remaining", "tok_reset_at")}
        self.headers_seen_at: float | None = None
        self.retry_after_until = 0.0
        self.day, self.day_requests, self.day_tokens = date.today().isoformat(), 0, 0
        self._load()

    # -- persistence (local day counters) ----------------------------------
    def _load(self) -> None:
        try:
            data = json.loads(config.usage_file(self.provider).read_text(encoding="utf-8"))
            if data.get("day") == self.day:
                self.day_requests = int(data.get("requests", 0))
                self.day_tokens = int(data.get("tokens", 0))
        except (OSError, ValueError):
            pass

    def _save(self) -> None:
        try:
            config.usage_file(self.provider).write_text(json.dumps({
                "day": self.day, "requests": self.day_requests, "tokens": self.day_tokens}), encoding="utf-8")
        except OSError:
            pass

    def _roll(self) -> None:
        now = time.time()
        while self.req_times and now - self.req_times[0] > 60:
            self.req_times.popleft()
        today = date.today().isoformat()
        if today != self.day:
            self.day, self.day_requests, self.day_tokens = today, 0, 0
            self._save()

    @property
    def live(self) -> bool:
        return self.headers_seen_at is not None

    def _rpm_limit(self) -> int:
        if self.p["requests_window"] == "minute" and self.h["req_limit"]:
            return int(self.h["req_limit"])
        return self.p["rpm"]()

    # -- fed by the httpx response hook --------------------------------------
    def record_headers(self, headers) -> None:
        def num(name: str) -> float | None:
            v = headers.get(name)
            try:
                return float(v) if v is not None else None
            except ValueError:
                return None

        now = time.time()
        with self.lock:
            if headers.get("x-ratelimit-limit-requests") or headers.get("x-ratelimit-limit-tokens"):
                rr = parse_duration(headers.get("x-ratelimit-reset-requests"))
                tr = parse_duration(headers.get("x-ratelimit-reset-tokens"))
                self.h.update({
                    "req_limit": num("x-ratelimit-limit-requests"),
                    "req_remaining": num("x-ratelimit-remaining-requests"),
                    "req_reset_at": now + rr if rr is not None else None,
                    "tok_limit": num("x-ratelimit-limit-tokens"),
                    "tok_remaining": num("x-ratelimit-remaining-tokens"),
                    "tok_reset_at": now + tr if tr is not None else None,
                })
                self.headers_seen_at = now
            ra = parse_duration(headers.get("retry-after"))
            if ra:
                self.retry_after_until = now + ra

    # -- hooks used by the callback handler ----------------------------------
    def wait_for_slot(self, on_wait=None, est_tokens: int = 1500) -> float:
        """Block until a request should fit: retry-after, tokens/min left, requests left."""
        waited = 0.0
        while True:
            with self.lock:
                self._roll()
                now, h = time.time(), self.h
                delay, why = 0.0, ""
                if self.retry_after_until > now:
                    delay, why = self.retry_after_until - now, "retry-after"
                elif len(self.req_times) >= self._rpm_limit():
                    delay, why = 60 - (now - self.req_times[0]) + 0.2, "RPM"
                elif (self.p["requests_window"] == "minute" and h["req_remaining"] is not None
                      and h["req_remaining"] < 1 and h["req_reset_at"] and h["req_reset_at"] > now):
                    delay, why = h["req_reset_at"] - now + 0.2, "RPM"
                elif (h["tok_remaining"] is not None and h["tok_reset_at"]
                      and h["tok_remaining"] < est_tokens and h["tok_reset_at"] > now):
                    delay, why = h["tok_reset_at"] - now + 0.2, "TPM"
                if delay <= 0:
                    self.req_times.append(now)
                    return waited
                if waited == 0:
                    self.session["throttle_waits"] += 1
            if on_wait and waited == 0:
                on_wait(delay, why)
            step = min(max(delay, 0.2), 2.0)
            time.sleep(step)
            waited += step
            if waited > 120:  # never hang forever; let the provider decide
                with self.lock:
                    self.req_times.append(time.time())
                return waited

    def record_success(self, input_tokens: int, output_tokens: int, latency: float) -> None:
        with self.lock:
            self._roll()
            s = self.session
            s["requests"] += 1
            s["input_tokens"] += input_tokens
            s["output_tokens"] += output_tokens
            s["total_tokens"] += input_tokens + output_tokens
            self.day_requests += 1
            self.day_tokens += input_tokens + output_tokens
            self.latencies.append(latency)
            self._save()

    def record_error(self, err: BaseException) -> bool:
        text = str(err)
        low = text.lower()
        is_429 = "429" in text or "rate_limit" in low or "rate limit" in low or "quota" in low
        with self.lock:
            self.session["errors_429" if is_429 else "errors_other"] += 1
            self.last_error = text[:300]
        return is_429

    # -- read model for the UI -----------------------------------------------
    def _header_meter(self, label: str, limit_k: str, rem_k: str, reset_k: str, fallback: int) -> dict:
        now, h = time.time(), self.h
        if self.live and h[limit_k] is not None:
            limit = int(h[limit_k])
            remaining = h[rem_k]
            if h[reset_k] and now >= h[reset_k]:
                remaining = limit  # window has reset since the last response
            used = limit - int(remaining if remaining is not None else limit)
            reset = _fmt_secs(h[reset_k] - now) if h[reset_k] and h[reset_k] > now else "now"
            return {"label": label, "used": used, "limit": limit, "source": "provider",
                    "note": f"Resets in {reset}"}
        return {"label": label, "used": 0, "limit": fallback, "source": "estimate",
                "note": "Estimate until the first response arrives"}

    def snapshot(self) -> dict:
        with self.lock:
            self._roll()
            p = self.p
            if p.get("local_rpm") and not self.live:
                meters = [{"label": "Requests per minute (RPM)", "used": len(self.req_times), "limit": p["rpm"](),
                           "source": "local", "note": "Rolling 60-second window (free tier ≈ 40 RPM; "
                                                      "NVIDIA publishes no token limits)"}]
            elif p["requests_window"] == "day":
                meters = [
                    self._header_meter("Requests per day (RPD)", "req_limit", "req_remaining", "req_reset_at", p["rpd"]()),
                    self._header_meter("Tokens per minute (TPM)", "tok_limit", "tok_remaining", "tok_reset_at", p["tpm"]()),
                    {"label": "Requests per minute (RPM)", "used": len(self.req_times), "limit": p["rpm"](),
                     "source": "local", "note": "Rolling 60-second window"},
                ]
            else:
                meters = [
                    self._header_meter("Requests per minute (RPM)", "req_limit", "req_remaining", "req_reset_at", p["rpm"]()),
                    self._header_meter("Tokens per minute (TPM)", "tok_limit", "tok_remaining", "tok_reset_at", p["tpm"]()),
                ]
            if p["tpd"]():
                meters.append({"label": "Tokens per day (TPD)", "used": self.day_tokens, "limit": p["tpd"](),
                               "source": "local", "note": "DevBox's own calls today"})
            if p["requests_window"] == "minute" and p["rpd"]():
                meters.append({"label": "Requests per day (RPD)", "used": self.day_requests, "limit": p["rpd"](),
                               "source": "local", "note": "DevBox's own calls today"})
            lat = list(self.latencies)
            return {
                "provider": self.provider, "provider_label": p["label"], "plan": p["plan"](),
                "limits_url": p["limits_url"],
                "live": self.live,
                "headers_age": _fmt_secs(time.time() - self.headers_seen_at) if self.live else None,
                "meters": meters,
                "day_requests": self.day_requests, "day_tokens": self.day_tokens,
                "last_latency": lat[-1] if lat else 0.0,
                "avg_latency": sum(lat) / len(lat) if lat else 0.0,
                "last_error": self.last_error,
                **self.session,
            }


trackers = {name: UsageTracker(name) for name in PROFILES}


def tracker(provider: str) -> UsageTracker:
    return trackers[provider]
