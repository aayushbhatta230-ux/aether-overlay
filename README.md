<div align="center">

# ✦ AETHER
### Ambient, local-first AI overlay for your Windows desktop

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10%2B-brightgreen.svg)](https://www.python.org/)
[![Platform: Windows](https://img.shields.io/badge/Platform-Windows%2010%20%7C%2011-0078D6.svg)](https://microsoft.com/windows)
[![LLM: Ollama](https://img.shields.io/badge/LLM-Llama%203.2%20(Local)-orange.svg)](https://ollama.com)

*AETHER floats a small, quiet card over your desktop. It sees a coarse, redacted
snapshot of what you're doing, asks a **local** model for one useful nudge, and
disappears again. It never acts on its own.*

</div>

---

## 🌟 Why this exists

Most "AI assistants" want your screen, your clipboard, your keystrokes, and a
monthly subscription. AETHER takes the opposite position:

| Principle | How it's enforced |
|---|---|
| 🧠 **100% local inference** | Ollama on `localhost`. No API keys, no egress, no telemetry. |
| 🔒 **Redacted before the model** | `aether/redact.py` scrubs emails, keys, cards, phones, IPs and user paths *before* any prompt is built. |
| 👁️ **Coarse context only** | Active app, window title, idle time, a clipboard **hash**, your git branch. No keystrokes, no screenshots, no file contents. |
| 🛡️ **Observes, never acts** | There is no execution path. `high` risk is withheld *and audited*; `medium` requires human approval. Asserted by tests. |
| 🤫 **Cannot nag** | Per-fingerprint cooldown, a hard hourly budget, "never show again", auto-hide when idle, and a no-op on an unchanged context. |
| 🗑️ **Forgettable** | `aether --clear` erases everything. History also expires on its own. |
| 🪶 **Zero required dependencies** | Core runs on the standard library alone. `tkinter` + `urllib` + `sqlite3`. |

## 🏛️ Architecture

```text
                  +-------------------------------+
                  |      AETHER Overlay (tk)      |
                  |  frameless · topmost · auto   |
                  |  hide · esc/s/a/q to decide  |
                  +---------------+---------------+
                                  |
           capture -> redact -> LLM -> risk gate -> dedupe -> present
                                  |
      +---------------------------+---------------------------+
      |            |              |              |            |
  [Context]   [Redactor]    [Ollama LLM]   [Risk Gate]   [Vault]
   window        PII          llama3.2      low/med/high   sqlite
   idle        scrubbing     JSON+timeout   approval      cooldown
   clipboard                                   policy       audit log
   git branch                                              + snoozes
   dwell                                                  + retention
```

**One cycle** (`Aether.tick()` in `aether/app.py`):

1. `ContextEngine.capture()` builds a redacted `ContextSnapshot` via injected probes.
2. Stay silent if you're idle, if suggestions are switched off, or if the hourly budget is spent.
3. Skip the round trip entirely if nothing about your context has changed.
4. `SuggestionEngine.generate()` asks Ollama, falling back to deterministic rules.
5. `partition_suggestions()` withholds `high` (audited), gates `medium`.
6. `Vault` suppresses anything snoozed or seen within the cooldown, then records the rest.
7. The overlay shows the card for `overlay_timeout_s` seconds.

### Keyboard

| Key | Effect |
|---|---|
| `Esc` | Hide the card |
| `d` | Dismiss - recorded, and used to tune confidence |
| `s` | **Never show me this again** - blocks the fingerprint for `dismiss_snooze_s` |
| `a` | This one was useful |
| `q` | Quit AETHER |

None of these perform the suggestion. That is the whole point.

### Threading model

Tk is not thread-safe, so the two halves of the process are strictly separated:

| Thread | Owns | Talks to the other via |
|---|---|---|
| **Main** | The Tk event loop, every widget, the auto-hide timers | `Overlay.post()` → `queue.Queue`, drained by a 100 ms `after` pump |
| **Worker** (`aether-poller`) | The polling loop, Ollama calls, the vault | `Aether.stop()` → `threading.Event`, so shutdown is immediate instead of waiting out `poll_interval_s` |

Running the polling loop on the main thread is what made the window report
"not responding" — the loop blocked the event loop for a full interval every
cycle. The tests assert the split so it cannot regress. Single-shot modes
(`--once`) render on the main thread instead, because there is no pump to hand
work to, and every wait is bounded by a deadline.

## 🚀 Quickstart

```bash
git clone https://github.com/aayushbhatta230-ux/aether-overlay
cd aether-overlay
pip install -e ".[win,dev]"

# Optional but recommended - the local brain
ollama serve
ollama pull llama3.2

python -m aether --doctor   # verify the environment
python -m aether            # run the overlay
```

Without Ollama running, AETHER still works - it falls back to deterministic
rule-based hints. You just get fewer (and blander) suggestions.

### CLI

| Command | What it does |
|---|---|
| `aether` | Run the overlay and the polling loop. |
| `aether --dry-run` | One cycle, printed to the terminal. No window, no nagging. |
| `aether --once` | A single cycle in a real window, then exit. |
| `aether --doctor` | Check Ollama, dependencies, and privacy settings. |
| `aether --history` | Recent suggestions plus the audit trail. |
| `aether --stats` | Counters by risk and source, plus your dismissal rate. |
| `aether --clear` | Erase recorded history (asks first). |
| `aether --clear --clear-older-than 7` | Drop only records older than 7 days. |
| `aether -v` | Verbose/debug logging. |

## ⚙️ Configuration

Layered: **defaults → JSON file → `AETHER_*` env vars**. Every layer is
type-checked and clamped, so a string where a number belongs can never reach
the runtime. An unknown key is an error, not a silent no-op.

```bash
python -m aether -c config.example.json
AETHER_OLLAMA_MODEL=phi3 AETHER_COOLDOWN_S=300 python -m aether
```

| Key | Default | Meaning |
|---|---|---|
| `ollama_model` | `llama3.2` | Local model to use. |
| `poll_interval_s` | `5.0` | Seconds between cycles. |
| `idle_threshold_s` | `180` | Go silent after this much idle time. |
| `cooldown_s` | `900` | Don't repeat a suggestion within this window. |
| `max_suggestions_per_hour` | `8` | Hard cap. `0` disables suggestions, `-1` removes the cap. |
| `dwell_reminder_s` | `2700` | Nudge after this long in one app. `0` disables. |
| `overlay_corner` | `bottom-right` | Screen corner. |
| `overlay_timeout_s` | `12.0` | Auto-hide delay. |
| `track_clipboard` | `true` | Track a clipboard **hash** (never contents). |
| `redact_pii` | `true` | Scrub PII before prompting. |
| `allow_medium_risk` | `true` | Show `medium` hints (flagged as gated). |
| `offline_fallback` | `true` | Use the deterministic rules when no model answers. |
| `history_retention_days` | `30` | History expires. `0` keeps everything. |
| `dismiss_snooze_s` | `86400` | How long `s` hides a suggestion. |

## 💡 What it notices

With no model at all, the rule engine still has something to say — always
`low` risk, because a hint changes nothing:

- A window that looks like a **failure** (error, traceback, panic, crash).
- A **meeting** (Zoom, Teams, Meet, and friends).
- **Unsaved changes** — the editor's `*` marker is treated as signal, not noise.
- A **long single-app session** past `dwell_reminder_s`.
- Work on a **non-default branch**.
- A **recent clipboard copy**.

## 🧪 Development

```bash
pytest -q                       # 174 tests, no desktop required
ruff check aether tests
pytest --cov=aether             # coverage
```

The whole system is built on **injected dependencies** - `Probes`, the LLM
`Transport`, and the `presenter` are all replaceable, so the test suite never
touches your real desktop, clipboard, or display. Overlay tests skip themselves
when no Tk display is available.

## 🗺️ Roadmap

- [x] M1 - package skeleton, config, overlay window
- [x] M2 - context engine with injectable probes
- [x] M3 - Ollama client, defensive parsing, offline fallback
- [x] M4 - risk gate, SQLite vault, audit trail
- [x] M5 - CLI, tests, CI
- [x] M6 - decisions on the card (dismiss / snooze / accept), activity stats, retention
- [ ] M7 - click-through HUD zones and tray icon
- [ ] M8 - optional cloud LLM backend (Groq/OpenRouter) for users without a GPU
- [ ] M9 - learn from dismissals to tune confidence

## 📄 License

MIT © Aayush Bhatta