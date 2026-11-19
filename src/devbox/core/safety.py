"""Guards that every tool must pass before touching the filesystem or processes."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import psutil

from devbox.core import config


class SafetyError(Exception):
    """Raised when an operation is not allowed."""


def _norm(p: Path) -> Path:
    return Path(os.path.normcase(os.path.abspath(p)))


def resolve_in_allowed(path_str: str) -> Path:
    """Resolve a user/LLM-supplied path and make sure it is inside an allowed root."""
    if not path_str or not path_str.strip():
        raise SafetyError("Empty path.")
    p = Path(os.path.expandvars(path_str.strip().strip('"'))).expanduser()
    if not p.is_absolute():
        p = config.HOME / p
    p = p.resolve(strict=False)
    np = _norm(p)

    for prot in config.PROTECTED_PATHS:
        npr = _norm(prot)
        if np == npr or np.is_relative_to(npr):
            raise SafetyError(f"'{p}' is inside protected location '{prot}'.")

    for root in config.ALLOWED_ROOTS:
        if np == _norm(root) or np.is_relative_to(_norm(root)):
            return p
    roots = ", ".join(str(r) for r in config.ALLOWED_ROOTS)
    raise SafetyError(f"'{p}' is outside the allowed folders ({roots}).")


def check_deletable(path_str: str) -> Path:
    """Stricter than resolve_in_allowed: also refuses allowed roots themselves."""
    p = resolve_in_allowed(path_str)
    np = _norm(p)
    if any(np == _norm(r) for r in config.ALLOWED_ROOTS):
        raise SafetyError(f"Refusing to delete an allowed root folder itself: '{p}'.")
    if np == _norm(config.HOME) or len(p.parts) <= 2:
        raise SafetyError(f"Refusing to delete top-level folder '{p}'.")
    for keep in config.NO_DELETE_PATHS:
        nk = _norm(keep)
        if np == nk or np.is_relative_to(nk) or nk.is_relative_to(np):
            raise SafetyError(f"Refusing to delete DevBox's own folder '{keep}'.")
    if not p.exists():
        raise SafetyError(f"'{p}' does not exist.")
    return p


def check_killable(pid: int) -> psutil.Process:
    if pid <= 4:
        raise SafetyError(f"PID {pid} is a core system process.")
    me = psutil.Process()
    family = {me.pid}
    try:
        family.update(p.pid for p in me.parents())
    except psutil.Error:
        pass
    if pid in family:
        raise SafetyError("Refusing to kill DevBox itself or its parent terminal.")
    try:
        proc = psutil.Process(pid)
        name = proc.name().lower()
    except psutil.NoSuchProcess as e:
        raise SafetyError(f"No process with PID {pid}.") from e
    except psutil.AccessDenied as e:
        raise SafetyError(f"Access denied to PID {pid} (system/elevated process).") from e
    if name in config.PROTECTED_PROCESSES:
        raise SafetyError(f"'{name}' is a protected Windows process.")
    return proc


def log_action(text: str) -> None:
    """Append an approved action to the audit log."""
    stamp = datetime.now().isoformat(timespec="seconds")
    with config.ACTION_LOG.open("a", encoding="utf-8") as f:
        f.write(f"{stamp}  {text}\n")
