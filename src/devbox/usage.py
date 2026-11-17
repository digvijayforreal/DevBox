"""Groq usage + rate-limit tracker.

Groq sends real rate-limit state in every response's headers:
  x-ratelimit-limit-requests / -remaining-requests / -reset-requests  -> requests per DAY
  x-ratelimit-limit-tokens   / -remaining-tokens   / -reset-tokens    -> tokens per MINUTE
  retry-after (only on 429)
DevBox captures those headers through an httpx response hook (see agents/graph.py),
so "left" numbers come from Groq itself. RPM and tokens/day aren't in the headers,
so those are counted locally against the limits in .env.
Limits apply to the whole Groq organization, not to one key.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import deque
from datetime import date

from devbox import config

_DUR = re.compile(r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m(?!s))?(?:(\d+(?:\.\d+)?)s)?(?:(\d+(?:\.\d+)?)ms)?")


def parse_duration(text: str | None) -> float | None:
    """'2m59.56s' -> 179.56, '7.66s' -> 7.66, '120ms' -> 0.12."""
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


class UsageTracker:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.req_times: deque[float] = deque()
        self.session = {"requests": 0, "input_tokens": 0, "output_tokens": 0,
                        "total_tokens": 0, "errors_429": 0, "errors_other": 0, "throttle_waits": 0}
        self.latencies: deque[float] = deque(maxlen=50)
        self.last_error = ""
        # Values from Groq headers (None until the first response).
        self.h: dict[str, float | None] = {
            "rpd_limit": None, "rpd_remaining": None, "rpd_reset_at": None,
            "tpm_limit": None, "tpm_remaining": None, "tpm_reset_at": None,
        }
        self.headers_seen_at: float | None = None
        self.retry_after_until = 0.0
        self.day, self.day_requests, self.day_tokens = date.today().isoformat(), 0, 0
        self._load()

    # -- persistence (local day counters) ----------------------------------
    def _load(self) -> None:
        try:
            data = json.loads(config.USAGE_FILE.read_text(encoding="utf-8"))
            if data.get("day") == self.day and data.get("model") == config.GROQ_MODEL:
                self.day_requests = int(data.get("requests", 0))
                self.day_tokens = int(data.get("tokens", 0))
        except (OSError, ValueError):
            pass

    def _save(self) -> None:
        try:
            config.USAGE_FILE.write_text(json.dumps({
                "day": self.day, "model": config.GROQ_MODEL,
                "requests": self.day_requests, "tokens": self.day_tokens}), encoding="utf-8")
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
            if headers.get("x-ratelimit-limit-tokens") is not None:
                rr = parse_duration(headers.get("x-ratelimit-reset-requests"))
                tr = parse_duration(headers.get("x-ratelimit-reset-tokens"))
                self.h.update({
                    "rpd_limit": num("x-ratelimit-limit-requests"),
                    "rpd_remaining": num("x-ratelimit-remaining-requests"),
                    "rpd_reset_at": now + rr if rr is not None else None,
                    "tpm_limit": num("x-ratelimit-limit-tokens"),
                    "tpm_remaining": num("x-ratelimit-remaining-tokens"),
                    "tpm_reset_at": now + tr if tr is not None else None,
                })
                self.headers_seen_at = now
            ra = parse_duration(headers.get("retry-after"))
            if ra:
                self.retry_after_until = now + ra

    # -- hooks used by the callback handler ----------------------------------
    def wait_for_slot(self, on_wait=None, est_tokens: int = 1500) -> float:
        """Block until a request should fit: Groq retry-after, TPM remaining, local RPM."""
        waited = 0.0
        while True:
            with self.lock:
                self._roll()
                now = time.time()
                delay, why = 0.0, ""
                if self.retry_after_until > now:
                    delay, why = self.retry_after_until - now, "Groq retry-after"
                elif len(self.req_times) >= config.GROQ_RPM_LIMIT:
                    delay, why = 60 - (now - self.req_times[0]) + 0.2, "RPM"
                elif (self.h["tpm_remaining"] is not None and self.h["tpm_reset_at"]
                      and self.h["tpm_remaining"] < est_tokens and self.h["tpm_reset_at"] > now):
                    delay, why = self.h["tpm_reset_at"] - now + 0.2, "TPM"
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
            if waited > 120:  # never hang forever; let Groq decide
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
        is_429 = "429" in text or "rate_limit" in text.lower() or "rate limit" in text.lower()
        with self.lock:
            self.session["errors_429" if is_429 else "errors_other"] += 1
            self.last_error = text[:300]
        return is_429

    # -- read model for the UI -----------------------------------------------
    def snapshot(self) -> dict:
        with self.lock:
            self._roll()
            now = time.time()
            h = self.h
            live = self.headers_seen_at is not None

            def left_in(at):
                return _fmt_secs(at - now) if at else "?"

            # After the reset time passes, the window is full again.
            tpm_remaining = h["tpm_remaining"]
            if live and h["tpm_reset_at"] and now >= h["tpm_reset_at"]:
                tpm_remaining = h["tpm_limit"]
            lat = list(self.latencies)
            return {
                "provider": "Groq", "model": config.GROQ_MODEL, "plan": config.GROQ_PLAN,
                "key_set": bool(config.GROQ_API_KEY),
                "live": live,
                "headers_age": _fmt_secs(now - self.headers_seen_at) if live else None,
                # From Groq headers (fallback: .env limits + local counts)
                "rpd_limit": int(h["rpd_limit"]) if live and h["rpd_limit"] is not None else config.GROQ_RPD_LIMIT,
                "rpd_remaining": int(h["rpd_remaining"]) if live and h["rpd_remaining"] is not None
                else max(config.GROQ_RPD_LIMIT - self.day_requests, 0),
                "rpd_reset_in": left_in(h["rpd_reset_at"]) if live else "?",
                "tpm_limit": int(h["tpm_limit"]) if live and h["tpm_limit"] is not None else config.GROQ_TPM_LIMIT,
                "tpm_remaining": int(tpm_remaining) if live and tpm_remaining is not None else config.GROQ_TPM_LIMIT,
                "tpm_reset_in": left_in(h["tpm_reset_at"]) if live else "?",
                # Local only
                "rpm_used": len(self.req_times), "rpm_limit": config.GROQ_RPM_LIMIT,
                "tpd_used": self.day_tokens, "tpd_limit": config.GROQ_TPD_LIMIT,
                "day_requests": self.day_requests,
                "last_latency": lat[-1] if lat else 0.0,
                "avg_latency": sum(lat) / len(lat) if lat else 0.0,
                "last_error": self.last_error,
                **self.session,
            }


usage = UsageTracker()
