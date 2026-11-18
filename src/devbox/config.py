"""Central configuration. Everything is overridable through `.env`."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

HOME = Path.home()


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _env_paths(name: str, default: list[Path]) -> list[Path]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return [Path(os.path.expandvars(p.strip())).expanduser() for p in raw.split(";") if p.strip()]


# --- Groq -------------------------------------------------------------------
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

# Groq returns the real RPD + TPM limits / remaining / reset in every response's
# headers, and DevBox uses those. These values are only used before the first
# response and for limits Groq doesn't send in headers (RPM, TPD).
# See https://console.groq.com/settings/limits for your organization's numbers.
GROQ_RPM_LIMIT = _env_int("GROQ_RPM_LIMIT", 30)
GROQ_RPD_LIMIT = _env_int("GROQ_RPD_LIMIT", 1000)
GROQ_TPM_LIMIT = _env_int("GROQ_TPM_LIMIT", 8000)
GROQ_TPD_LIMIT = _env_int("GROQ_TPD_LIMIT", 200_000)
GROQ_PLAN = os.getenv("GROQ_PLAN", "Free")

# --- OpenAI -----------------------------------------------------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
# Empty = pick the first chat model from your account's /v1/models list.
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "")
# OpenAI sends real per-minute limits (requests + tokens) in response headers.
# These are only used before the first response. Requests/day is counted locally
# (0 = no daily limit shown). Your limits: https://platform.openai.com/settings/organization/limits
OPENAI_RPM_LIMIT = _env_int("OPENAI_RPM_LIMIT", 500)
OPENAI_TPM_LIMIT = _env_int("OPENAI_TPM_LIMIT", 200_000)
OPENAI_RPD_LIMIT = _env_int("OPENAI_RPD_LIMIT", 0)
OPENAI_TIER = os.getenv("OPENAI_TIER", "Tier 1")

# Reasoning effort for reasoning models (Groq gpt-oss): low | medium | high | "" (provider default)
REASONING_EFFORT = os.getenv("DEVBOX_REASONING_EFFORT", "low").strip().lower()

# --- NVIDIA (build.nvidia.com / NIM, OpenAI-compatible) ---------------------
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
NVIDIA_MODEL = os.getenv("NVIDIA_MODEL", "mistralai/mistral-large-4-1t-a52b")
NVIDIA_BASE_URL = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
# Free tier is ~40 requests/minute (varies by model + traffic; no official token limits).
NVIDIA_RPM_LIMIT = _env_int("NVIDIA_RPM_LIMIT", 40)
NVIDIA_PLAN = os.getenv("NVIDIA_PLAN", "Free")

# --- Active provider ----------------------------------------------------------
# "groq" or "openai". The model picked in Settings overrides this (saved in .devbox/).
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "").strip().lower()

# --- Filesystem safety --------------------------------------------------------
ALLOWED_ROOTS: list[Path] = _env_paths(
    "DEVBOX_ALLOWED_ROOTS",
    [HOME / "Desktop", HOME / "Downloads", HOME / "Documents"],
)

_sysdrive = Path(os.environ.get("SystemDrive", "C:") + "\\")
PROTECTED_PATHS: list[Path] = [
    Path(os.environ.get("SystemRoot", r"C:\Windows")),
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
    Path(os.environ.get("ProgramData", r"C:\ProgramData")),
    _sysdrive / "System Volume Information",
    _sysdrive / "$Recycle.Bin",
]
# Readable, but never deletable.
NO_DELETE_PATHS: list[Path] = [PROJECT_ROOT]

# --- Process safety -----------------------------------------------------------
PROTECTED_PROCESSES = {
    "system", "system idle process", "registry", "smss.exe", "csrss.exe",
    "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe", "svchost.exe",
    "explorer.exe", "dwm.exe", "fontdrvhost.exe", "memcompression",
    "secure system", "msmpeng.exe", "sihost.exe", "ctfmon.exe",
}

# --- Web UI -------------------------------------------------------------------
# Always bound to 127.0.0.1 (never exposed to the network).
WEB_PORT = _env_int("DEVBOX_PORT", 8765)
OPEN_BROWSER = os.getenv("DEVBOX_OPEN_BROWSER", "1") not in ("0", "false", "no")

# --- Limits -------------------------------------------------------------------
SCAN_TIME_LIMIT_S = _env_int("DEVBOX_SCAN_TIME_LIMIT", 25)
LLM_TOOL_OUTPUT_CHARS = 4000  # cap on tool output the LLM reads
MAX_HANDOFFS_PER_TURN = 4

LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
ACTION_LOG = LOG_DIR / "devbox_actions.log"
ACTIVITY_LOG = LOG_DIR / "devbox_activity.log"
STATE_DIR = PROJECT_ROOT / ".devbox"  # app state (selected model, usage counters)
STATE_DIR.mkdir(exist_ok=True)
SELECTION_FILE = STATE_DIR / "model.json"


def usage_file(provider: str):
    return STATE_DIR / f"usage_{provider}.json"
