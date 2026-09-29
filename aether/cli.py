"""AETHER command-line entrypoint.

Examples::

    aether              # run the overlay
    aether --dry-run    # one cycle, printed to the terminal, no window
    aether --doctor     # check the environment (Ollama, deps, config)
    aether --history    # recent suggestions and the audit trail
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
from collections.abc import Sequence

from . import __version__
from .app import Aether
from .config import load_config
from .llm import HttpOllamaTransport
from .overlay import Overlay, console_fallback
from .vault import Vault

log = logging.getLogger("aether")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _cmd_doctor(config) -> int:
    """Report environment readiness. Never raises, always explains."""
    ok = True
    print(f"AETHER {__version__}")
    print(f"  config dir : {config.config_dir}")
    print(f"  database   : {config.db_file}")

    try:
        import ollama  # noqa: F401
        client_present = True
    except ImportError:
        client_present = False

    try:
        HttpOllamaTransport(config.ollama_host).generate(
            config.ollama_model, "ping", "reply ok", 3.0
        )
        llm_up = True
    except Exception as exc:
        llm_up = False
        print(f"  ! Ollama unreachable at {config.ollama_host} ({exc})")

    print(f"  ollama pypi: {'installed' if client_present else 'not installed (using urllib)'}")
    print(f"  llm online : {'yes' if llm_up else 'no (rule fallback active)'}")

    import tkinter  # noqa: F401

    print("  overlay    : tkinter available")
    if not llm_up:
        ok = False
        print("\n  Tip: run `ollama serve` and `ollama pull "
              f"{config.ollama_model}` for full suggestions.")
    print("\n  Ready." if ok else "\n  Degraded (rules only).")
    return 0


def _cmd_history(config) -> int:
    vault = Vault(config.db_file)
    recent = vault.recent(limit=15)
    if not recent:
        print("No suggestions recorded yet.")
        return 0
    print("Recent suggestions\n" + "-" * 60)
    for s in recent:
        mark = {"dismissed": "x", "accepted": "v"}.get(s.decision or "", " ")
        print(f"[{mark}] {s.risk:<6} {s.source:<5} {s.title}")
    trail = vault.audit_trail(limit=5)
    if trail:
        print("\nAudit trail\n" + "-" * 60)
        for entry in trail:
            print(f"  {entry['event']}: {entry['detail']}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="aether", description="Ambient AI overlay for the Windows desktop."
    )
    parser.add_argument("--version", action="version", version=f"aether {__version__}")
    parser.add_argument("-c", "--config", help="path to a JSON config file")
    parser.add_argument("--dry-run", action="store_true",
                        help="run one cycle and print instead of showing a window")
    parser.add_argument("--once", action="store_true", help="run a single cycle and exit")
    parser.add_argument("--doctor", action="store_true", help="check the environment")
    parser.add_argument("--history", action="store_true", help="show recent activity")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)

    try:
        config = load_config(args.config)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.doctor:
        return _cmd_doctor(config)
    if args.history:
        return _cmd_history(config)

    if args.dry_run:
        aether = Aether(config, presenter=lambda _s: None)
        shown = aether.tick()
        if not shown:
            print("No suggestion right now (idle, rate-limited, or nothing useful).")
        else:
            for s in shown:
                print(console_fallback(s))
        return 0

    overlay = Overlay(config)
    # Tk is not thread-safe and its main loop must own this thread, so the
    # polling loop runs on a worker and hands cards over via overlay.post.
    aether = Aether(config, presenter=overlay.post)

    if args.once:
        aether.tick()
        overlay.wait_for_exit(poll_ms=min(int(config.overlay_timeout_s * 1000), 250))
        overlay.destroy()
        return 0

    worker = threading.Thread(
        target=aether.run_forever, name="aether-poller", daemon=True
    )
    worker.start()
    try:
        overlay.run()
    except KeyboardInterrupt:
        log.info("interrupted; shutting down")
    finally:
        aether.stop()
        worker.join(timeout=5.0)
        overlay.destroy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
