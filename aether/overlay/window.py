"""The overlay: a frameless, always-on-top suggestion card.

Built on ``tkinter`` (stdlib) so the HUD works with zero extra dependencies.
On non-Windows platforms the window still renders - it simply cannot be
topmost, and the code says so rather than pretending.

Interaction is deliberately inert: ``Esc`` hides, ``d`` records a dismissal,
``s`` snoozes a suggestion for a day, ``a`` marks it useful, ``q`` quits.
None of them *perform* the suggestion - that is what keeps the
observe-never-act invariant true at the UI layer.
"""

from __future__ import annotations

import logging
import platform
import queue
import time
import tkinter as tk
from collections.abc import Callable

from ..config import Config
from ..models import Suggestion

log = logging.getLogger(__name__)

IS_WINDOWS = platform.system() == "Windows"

#: Risk -> accent colour.
_RISK_COLOURS = {
    "low": "#4ADE80",     # green
    "medium": "#FBBF24",  # amber
    "high": "#F87171",    # red
}

_BG = "#11151C"
_FG = "#E6EDF3"
_MUTED = "#8B949E"

#: ``(suggestion_id, decision, snooze)`` handed back to the orchestrator.
DecisionHook = Callable[[str, str, bool], None]


class Overlay:
    """A single suggestion card that floats over everything else.

    The overlay is *display only*. It has no click handlers that trigger
    actions - reporting a decision back to the orchestrator is the only thing
    a key press can do.
    """

    def __init__(self, config: Config, on_decision: DecisionHook | None = None) -> None:
        self.config = config
        self.on_decision = on_decision
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("AETHER")
        self.root.configure(bg=_BG)
        self.root.resizable(False, False)
        self.root.attributes("-topmost", config.overlay_always_on_top)
        try:
            self.root.attributes("-alpha", config.overlay_opacity)
        except tk.TclError as exc:  # pragma: no cover - platform dependent
            log.debug("alpha unsupported: %s", exc)

        self.card = tk.Frame(self.root, bg=_BG, padx=18, pady=14)
        self.card.pack()

        self.dot = tk.Label(self.card, text="●", bg=_BG, fg=_MUTED, font=("Segoe UI", 10))
        self.dot.grid(row=0, column=0, sticky="w", padx=(0, 8))

        self.title_label = tk.Label(
            self.card, text="", bg=_BG, fg=_FG,
            font=("Segoe UI Semibold", 11), anchor="w", justify="left",
        )
        self.title_label.grid(row=0, column=1, sticky="w")

        self.body_label = tk.Label(
            self.card, text="", bg=_BG, fg=_MUTED,
            font=("Segoe UI", 9), anchor="w", justify="left", wraplength=360,
        )
        self.body_label.grid(row=1, column=1, sticky="w", pady=(4, 0))

        self.footer = tk.Label(
            self.card, text="AETHER · local only · esc to dismiss",
            bg=_BG, fg=_MUTED, font=("Segoe UI", 7),
        )
        self.footer.grid(row=2, column=1, sticky="w", pady=(10, 0))

        self.root.bind("<Escape>", lambda _e: self.hide())
        self.root.bind("q", self._on_quit)
        self.root.bind("d", lambda _e: self._decide("dismissed", snooze=False))
        self.root.bind("s", lambda _e: self._decide("snoozed", snooze=True))
        self.root.bind("a", lambda _e: self._decide("accepted", snooze=False))
        self._after_id: str | None = None
        self._pump_id: str | None = None
        #: Tk is not thread-safe. The polling loop runs on a worker thread and
        #: hands suggestions over this queue; a Tk timer drains it on the UI
        #: thread. Never touch a widget from another thread.
        self._inbox: queue.Queue[Suggestion] = queue.Queue()
        #: True while :meth:`run` owns the Tk main loop. The pump only reschedules
        #: itself while this is set, so timers stop at shutdown.
        self._running = False
        #: The card currently on screen, so a key press knows what it is about.
        self._current: Suggestion | None = None
        #: Optional callback invoked when the window is asked to close.
        self._on_close = None
        self._position()

    # -- geometry --------------------------------------------------------
    def _position(self) -> None:
        """Anchor the card to the configured screen corner with a margin."""
        self.root.update_idletasks()
        w = self.root.winfo_width() or 400
        h = self.root.winfo_height() or 120
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        m = 32

        pos = {
            "bottom-right": (sw - w - m, sh - h - m),
            "bottom-left": (m, sh - h - m),
            "top-right": (sw - w - m, m),
            "top-left": (m, m),
        }[self.config.overlay_corner]
        self.root.geometry(f"+{pos[0]}+{pos[1]}")

    # -- display ---------------------------------------------------------
    def show(self, suggestion: Suggestion) -> None:
        """Render a suggestion and auto-hide it after the configured timeout."""
        self._cancel_timer()
        self._current = suggestion
        self.title_label.configure(text=suggestion.title[:60])
        self.body_label.configure(text=suggestion.body[:180])
        self.dot.configure(fg=_RISK_COLOURS.get(suggestion.risk, _MUTED))

        gated = " · approval required" if suggestion.is_gated else ""
        self.footer.configure(
            text=f"AETHER · {suggestion.source}{gated} · esc hide · s snooze · q quit"
        )

        self.root.deiconify()
        self.root.lift()
        if IS_WINDOWS and self.config.overlay_always_on_top:
            self.root.attributes("-topmost", True)
        self._position()
        self._after_id = self.root.after(
            int(self.config.overlay_timeout_s * 1000), self.hide
        )

    def present(self, suggestion: Suggestion) -> None:
        """Show ``suggestion`` on the **calling (UI) thread**.

        Used by the single-shot paths, where there is no running pump to hand
        the work to. Calling :meth:`show` from a worker thread is a Tcl
        violation, so those paths must not use :meth:`post`.
        """
        self.show(suggestion)

    def hide(self) -> None:
        """Dismiss the card. Called by the auto-hide timer and by key presses."""
        self._cancel_timer()
        self._current = None
        self.root.withdraw()

    def _cancel_timer(self) -> None:
        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except tk.TclError:  # pragma: no cover
                pass
            self._after_id = None

    def destroy(self) -> None:
        self._cancel_timer()
        if self._pump_id is not None:
            try:
                self.root.after_cancel(self._pump_id)
            except tk.TclError:  # pragma: no cover
                pass
            self._pump_id = None
        try:
            self.root.destroy()
        except tk.TclError:  # pragma: no cover
            pass

    # -- decisions -------------------------------------------------------
    def _decide(self, decision: str, snooze: bool) -> None:
        """Report the decision on the current card, then dismiss it."""
        suggestion, self._current = self._current, None
        self.hide()
        if suggestion is None or self.on_decision is None:
            return
        try:
            self.on_decision(suggestion.id, decision, snooze)
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("decision hook failed: %s", exc)

    def _on_quit(self, _event=None) -> None:
        if self._current is not None and self.on_decision is not None:
            try:
                self.on_decision(self._current.id, "dismissed", False)
            except Exception:  # pragma: no cover - defensive
                pass
        self.request_close()

    def request_close(self, on_close: Callable[[], None] | None = None) -> None:
        """Ask the main loop to shut down. Callable from any thread."""
        if on_close is not None:
            self._on_close = on_close
        try:
            self.root.quit()  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - Tk is not thread-safe
            # ``root.quit`` from a worker thread can raise if the interpreter is
            # finalising. The window is torn down by the caller's finally block.
            pass

    # -- loop ------------------------------------------------------------
    def run(self) -> None:
        """Own the Tk main loop. Blocks until the window is destroyed.

        This **must** be called on the thread that created the window. The
        polling loop belongs on a separate worker thread and communicates through
        :meth:`post`; blocking this thread with the polling loop is what made the
        window report "not responding".
        """
        self._running = True
        self._schedule_pump()
        try:
            self.root.mainloop()
        finally:
            self._running = False
            if self._on_close is not None:
                callback, self._on_close = self._on_close, None
                callback()

    def post(self, suggestion: Suggestion) -> None:
        """Thread-safe entry point for the presenter.

        Safe to call from the polling worker thread: it only enqueues, and the Tk
        timer callback does the actual widget work.
        """
        self._inbox.put(suggestion)

    def _schedule_pump(self) -> None:
        try:
            self._pump_id = self.root.after(100, self._pump)
        except tk.TclError:  # pragma: no cover - window already destroyed
            self._pump_id = None

    def _pump(self) -> None:
        """Drain pending suggestions on the Tk thread."""
        self._pump_id = None
        try:
            while True:
                self.show(self._inbox.get_nowait())
        except queue.Empty:
            pass
        except tk.TclError:  # pragma: no cover - destroyed mid-drain
            return
        if self._running:
            self._schedule_pump()

    def wait_for_exit(self, poll_ms: int = 200, timeout_s: float | None = None) -> bool:
        """Pump the Tk event loop until the card is gone or ``timeout_s`` elapses.

        Returns ``True`` if the card closed on its own, ``False`` on timeout.
        The bound matters: without it a card that never closes would hang the
        caller forever. With no card on screen it returns straight away.
        """
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        while True:
            try:
                self.root.update()
                alive = self.root.winfo_exists()
            except tk.TclError:
                return True
            if not alive:
                return True
            if self._after_id is None and self._current is None:
                return True
            if deadline is not None and time.monotonic() >= deadline:
                return False
            self.root.after(poll_ms)


def console_fallback(suggestion: Suggestion) -> str:
    """Plain-text rendering for terminals and for ``--dry-run``.

    Useful for CI, for machines with no display, and for verifying the risk
    gate without ever flashing a window at someone.
    """
    bar = {"low": "OK  ", "medium": "GATE", "high": "HOLD"}[suggestion.risk]
    lines = [
        f"[{bar}] {suggestion.title}",
        f"       {suggestion.body}",
        f"       context: {suggestion.source}"
        + ("  · approval required - AETHER will not act" if suggestion.is_gated else ""),
    ]
    return "\n".join(lines)