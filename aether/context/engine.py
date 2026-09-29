"""The context engine: build redacted :class:`ContextSnapshot` objects.

Every source of truth is injected as a small callable, so the whole engine is
unit-testable on any OS without a real desktop, clipboard, or git install.
On Windows the real implementations lazily import ``pywin32``/``pyperclip``;
elsewhere the engine degrades to inert defaults.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from ..config import Config
from ..models import ContextSnapshot
from ..redact import fingerprint_clipboard, normalize_title, redact, strip_modified_marker

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Default probes. Each one is a no-op when its optional dependency is missing.
# --------------------------------------------------------------------------
def _probe_active_window() -> tuple[str, str]:
    """Return ``(app_name, window_title)`` for the foreground window."""
    try:
        import win32gui
        import win32process
    except ImportError:
        return ("unknown", "")

    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return ("unknown", "")
        title = win32gui.GetWindowText(hwnd) or ""
        pid = win32process.GetWindowThreadProcessId(hwnd)[1]
        try:
            import psutil

            app = psutil.Process(pid).name()
        except Exception:
            app = "unknown"
    except Exception as exc:  # pragma: no cover - depends on live desktop
        log.debug("active window probe failed: %s", exc)
        return ("unknown", "")
    return (app or "unknown", title)


def _probe_idle_seconds() -> float:
    """Seconds since the last user input, via Win32 ``GetLastInputInfo``."""
    try:
        import ctypes
    except ImportError:
        return 0.0

    class _LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_ulong)]

    try:  # pragma: no cover - depends on live desktop
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        millis = ctypes.windll.kernel32.GetTickCount() - info.dwTime
        return max(0.0, millis / 1000.0)
    except Exception as exc:
        log.debug("idle probe failed: %s", exc)
        return 0.0


def _probe_clipboard() -> str | None:
    try:
        import pyperclip
    except ImportError:
        return None
    try:
        return pyperclip.paste()
    except Exception as exc:
        log.debug("clipboard probe failed: %s", exc)
        return None


def _probe_git_branch(cwd: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("git probe failed: %s", exc)
        return None
    branch = (result.stdout or "").strip()
    if not branch or result.returncode != 0:
        return None
    return None if branch == "HEAD" else branch


@dataclass(slots=True)
class Probes:
    """Injectable set of context probes. All fields are zero-arg callables."""

    active_window: Callable[[], tuple[str, str]] = _probe_active_window
    idle_seconds: Callable[[], float] = _probe_idle_seconds
    clipboard: Callable[[], str | None] = _probe_clipboard
    git_branch: Callable[[str], str | None] = _probe_git_branch


@dataclass(slots=True)
class _Dwell:
    """Tracks how long the foreground application has not changed."""

    app: str = ""
    since: float = field(default_factory=time.monotonic)

    def seconds_in(self, app: str) -> float:
        if app != self.app:
            self.app = app
            self.since = time.monotonic()
            return 0.0
        return max(0.0, time.monotonic() - self.since)


class ContextEngine:
    """Assembles redacted snapshots on demand."""

    def __init__(self, config: Config, probes: Probes | None = None) -> None:
        self.config = config
        self.probes = probes or Probes()
        self._last_clip_hash: str | None = None
        self._dwell = _Dwell()

    def capture(self, cwd: str | None = None) -> ContextSnapshot:
        """Take one snapshot, applying redaction and every config toggle."""
        app, title = self._safe(self.probes.active_window, ("unknown", ""))
        modified, title = strip_modified_marker(str(title or ""))
        title = normalize_title(title)
        if self.config.redact_pii:
            app = redact(str(app or "unknown"))
            title = redact(title)
        app = app or "unknown"

        idle = float(self._safe(self.probes.idle_seconds, 0.0) or 0.0)

        clip_hash: str | None = None
        if self.config.track_clipboard:
            raw_clip = self._safe(self.probes.clipboard, None)
            clip_hash = fingerprint_clipboard(raw_clip or "")
            if clip_hash == self._last_clip_hash:
                # Unchanged clipboard: drop it so the LLM is not told about a
                # stale copy-paste repeatedly.
                clip_hash = None
            else:
                self._last_clip_hash = clip_hash

        branch: str | None = None
        workdir = cwd or os.getcwd()
        if self.config.track_git_branch:
            branch = self._safe(lambda: self.probes.git_branch(workdir), None)

        dwell = self._dwell.seconds_in(app) if idle < 5.0 else 0.0

        snap = ContextSnapshot(
            active_app=app,
            window_title=title,
            idle_seconds=idle,
            clipboard_hash=clip_hash,
            git_branch=branch,
            working_dir=None,  # never exposed: paths are identifying
            app_dwell_seconds=dwell,
            document_modified=modified,
        )
        log.debug("captured context: %s", snap.to_llm_prompt().replace("\n", " | "))
        return snap

    @staticmethod
    def _safe(fn: Callable, fallback):
        try:
            return fn()
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("probe %r raised: %s", getattr(fn, "__name__", fn), exc)
            return fallback