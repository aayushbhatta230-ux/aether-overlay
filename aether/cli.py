"""AETHER command-line entrypoint.

Examples::

    aether              # run the overlay
    aether --dry-run    # one cycle, printed to the terminal, no window
    aether --once       # one cycle shown in a real window, then exit
    aether --doctor     # check the environment (Ollama, deps, config)
    aether --history    # recent suggestions and the audit trail
    aether --stats      # activity counters, by risk and by source
    aether --clear      # forget recorded history (privacy)
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from collections.abc import Sequence

from . import __version__
from .app import Aether
from .config import load_config
from .llm import HttpOllamaTransport
from .overlay import Overlay, console_fallback
from .safety import never_auto_execute
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

    llm_up = False
    try:
        HttpOllamaTransport(config.ollama_host).generate(
            config.ollama_model, "ping", "reply ok", 3.0
        )
        llm_up = True
    except Exception as exc:
        llm_up = False
        ok = False
        print(f"  ! Ollama unreachable at {config.ollama_host} ({exc})")

    print(f"  ollama pypi: {'installed' if client_present else 'not installed (using urllib)'}")
    print(f"  llm online : {'yes' if llm_up else 'no (rule fallback active)'}")

    try:
        import tkinter  # noqa: F401

        print("  overlay    : tkinter available")
    except ImportError:
        ok = False
        print("  overlay    : tkinter MISSING - use --dry-run")

    print(f"  rules      : {'on' if config.offline_fallback else 'off'}")
    if not config.suggestions_enabled:
        print("  suggestions: DISABLED (max_suggestions_per_hour = 0)")
    elif config.rate_capped:
        print(f"  budget     : {config.max_suggestions_per_hour}/hour")
    else:
        print("  budget     : uncapped (-1) - AETHER can nag; this is discouraged")

    if config.redact_pii:
        print("  redaction  : on")
    else:
        ok = False
        print("  ! redaction is OFF - context will not be scrubbed before prompting")

    print(f"  retention  : {config.history_retention_days or 'forever'} day(s)")
    print(f"  auto-exec  : {'ENABLED (!)' if never_auto_execute() else 'NEVER'}")

    if not llm_up:
        print("\n  Tip: run `ollama serve` and `ollama pull "
              f"{config.ollama_model}` for full suggestions.")
    print("\n  Ready." if ok else "\n  Degraded.")
    return 0


def _cmd_history(config) -> int:
    vault = Vault(config.db_file)
    recent = vault.recent(limit=15)
    if not recent:
        print("No suggestions recorded yet.")
        return 0
    print("Recent suggestions\n" + "-" * 60)
    for s in recent:
        mark = {"dismissed": "x", "accepted": "v", "snoozed": "-"}.get(s.decision or "", " ")
        print(f"[{mark}] {s.risk:<6} {s.source:<5} {s.title}")
    trail = vault.audit_trail(limit=5)
    if trail:
        print("\nAudit trail\n" + "-" * 60)
        for entry in trail:
            print(f"  {entry['event']}: {entry['detail']}")
    return 0


def _cmd_stats(config) -> int:
    vault = Vault(config.db_file)
    stats = vault.stats()
    print(f"AETHER activity\n{'-' * 40}")
    print(f"  database        : {stats['db_path']}")
    print(f"  shown (all time): {stats['total']}")
    print(f"  last hour       : {stats['last_hour']}")
    print(f"  last 24h        : {stats['last_24h']}")
    print(f"  dismissed       : {stats['dismissed']}")
    print(f"  accepted        : {stats['accepted']}")
    print(f"  snoozed now     : {stats['suppressed']}")

    if stats["total"]:
        shown = stats["total"] or 1
        dismissed = 100.0 * stats["dismissed"] / shown
        accepted = 100.0 * stats["accepted"] / shown
        print(f"  dismissal rate  : {dismissed:.0f}%")
        print(f"  acceptance rate : {accepted:.0f}%")
        if dismissed >= 60:
            print(
                "  hint: most hints are being ignored - try a higher "
                "cooldown_s or max_suggestions_per_hour = 0."
            )

    if stats["by_risk"]:
        print("\n  by risk")
        for risk in ("low", "medium", "high"):
            if risk in stats["by_risk"]:
                print(f"    {risk:<7} {stats['by_risk'][risk]}")
    if stats["by_source"]:
        print("\n  by source")
        for source, count in sorted(stats["by_source"].items()):
            print(f"    {source:<7} {count}")

    if stats["oldest"]:
        age_days = (time.time() - stats["oldest"]) / 86400.0
        print(f"\n  oldest record   : {age_days:.1f} day(s) ago")
    return 0


def _cmd_clear(config, days: int | None, assume_yes: bool) -> int:
    vault = Vault(config.db_file)
    if days:
        counts = vault.purge(older_than_days=days)
        total = sum(counts.values())
        print(f"Removed {total} record(s) older than {days} day(s): {counts}")
        return 0
    if not assume_yes:
        try:
            reply = input("Erase all AETHER history? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            reply = ""
        if reply not in ("y", "yes"):
            print("Cancelled.")
            return 1
    counts = vault.purge()
    print(f"Cleared AETHER history: {counts}")
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
    parser.add_argument("--stats", action="store_true", help="show activity counters")
    parser.add_argument("--clear", action="store_true", help="erase recorded history")
    parser.add_argument("--clear-older-than", type=int, metavar="DAYS", default=None,
                        help="with --clear, drop only records older than DAYS")
    parser.add_argument("--yes", action="store_true", help="assume yes for --clear")
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
    if args.stats:
        return _cmd_stats(config)
    if args.clear:
        return _cmd_clear(config, args.clear_older_than, args.yes)

    if args.dry_run:
        aether = Aether(config, presenter=lambda _s: None)
        shown = aether.tick()
        if not shown:
            print("No suggestion right now (idle, rate-limited, or nothing useful).")
        else:
            for s in shown:
                print(console_fallback(s))
        print(f"\ncontext: {aether.stats.as_dict()}")
        return 0

    if args.once:
        # Single-shot: there is no pump to hand work to, so the card is
        # rendered directly on this (UI) thread and given a hard deadline.
        overlay = Overlay(config)
        aether = Aether(config, presenter=overlay.present)
        overlay.on_decision = aether.decide
        try:
            shown = aether.tick()
            if not shown:
                print("No suggestion right now (idle, rate-limited, or nothing useful).")
            timeout = config.overlay_timeout_s + 2.0
            overlay.wait_for_exit(poll_ms=100, timeout_s=timeout)
        finally:
            overlay.destroy()
        return 0

    overlay = Overlay(config)
    # Tk is not thread-safe and its main loop must own this thread, so the
    # polling loop runs on a worker and hands cards over via overlay.post.
    aether = Aether(config, presenter=overlay.post)
    overlay.on_decision = aether.decide

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