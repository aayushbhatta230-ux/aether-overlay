"""The overlay: a frameless, always-on-top suggestion card.

Built on ``tkinter`` (stdlib) so the HUD works with zero extra dependencies.
On non-Windows platforms the window still renders - it simply cannot be
topmost or click-through, and the code says so rather than pretending.
"""

from __future__ import annotations

import logging
import platform
import tkinter as tk

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


class Overlay:
    """A single suggestion card that floats over everything else.

    The overlay is *display only*. It has no click handlers that trigger
    actions - dismissing is the only interaction, which is what keeps the
    observe-never-act invariant true at the UI layer.
    """

    def __init__(self, config: Config) -> None:
        self.config = config
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
        self._after_id: str | None = None
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
        self.title_label.configure(text=suggestion.title[:60])
        self.body_label.configure(text=suggestion.body[:180])
        self.dot.configure(fg=_RISK_COLOURS.get(suggestion.risk, _MUTED))

        gated = " · approval required" if suggestion.is_gated else ""
        self.footer.configure(text=f"AETHER · {suggestion.source}{gated} · esc to dismiss")

        self.root.deiconify()
        self.root.lift()
        if IS_WINDOWS and self.config.overlay_always_on_top:
            self.root.attributes("-topmost", True)
        self._position()
        self._after_id = self.root.after(
            int(self.config.overlay_timeout_s * 1000), self.hide
        )

    def hide(self) -> None:
        self._cancel_timer()
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
        try:
            self.root.destroy()
        except tk.TclError:  # pragma: no cover
            pass

    def run(self) -> None:
        """Block on the Tk event loop (used when AETHER runs headless-visible)."""
        self.root.mainloop()


def console_fallback(suggestion: Suggestion) -> str:
    """Plain-text rendering for terminals and for ``--dry-run``.

    Useful for CI, for machines with no display, and for verifying the risk
    gate without ever flashing a window at someone.
    """
    bar = {"low": "OK  ", "medium": "GATE", "high": "HOLD"}[suggestion.risk]
    lines = [f"[{bar}] {suggestion.title}", f"       {suggestion.body}"]
    if suggestion.is_gated:
        lines.append("       (approval required - AETHER will not act)")
    return "\n".join(lines)
