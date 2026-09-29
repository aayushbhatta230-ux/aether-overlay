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
| 🛡️ **Observes, never acts** | There is no execution path. `high` risk is withheld entirely; `medium` requires human approval. Asserted by tests. |
| 🤫 **Cannot nag** | Per-fingerprint cooldown + a hard hourly budget + auto-hide when you're idle. |
| 🪶 **Zero required dependencies** | Core runs on the standard library alone. `tkinter` + `urllib` + `sqlite3`. |

## 🏛️ Architecture

```text
                 +-------------------------------+
                 |      AETHER Overlay (tk)      |
                 |  frameless · topmost · auto   |
                 |  hide · Esc to dismiss        |
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
  git branch                                              + history
```

**One cycle** (`Aether.tick()` in `aether/app.py`):

1. `ContextEngine.capture()` builds a redacted `ContextSnapshot` via injected probes.
2. Stay silent if you're idle, or if the hourly budget is spent.
3. `SuggestionEngine.generate()` asks Ollama, falling back to deterministic rules.
4. `safety.filter_suggestions()` drops `high` risk and flags `medium` as gated.
5. `Vault` suppresses anything seen within the cooldown, then records the rest.
6. The overlay shows the card for `overlay_timeout_s` seconds.

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
| `aether --once` | A single cycle, then exit. |
| `aether --doctor` | Check Ollama, dependencies, and config paths. |
| `aether --history` | Recent suggestions plus the audit trail. |
| `aether -v` | Verbose/debug logging. |

## ⚙️ Configuration

Layered: **defaults → JSON file → `AETHER_*` env vars**.

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
| `max_suggestions_per_hour` | `8` | Hard cap. `0` disables suggestions. |
| `overlay_corner` | `bottom-right` | Screen corner. |
| `overlay_timeout_s` | `12.0` | Auto-hide delay. |
| `track_clipboard` | `true` | Track a clipboard **hash** (never contents). |
| `redact_pii` | `true` | Scrub PII before prompting. |

## 🧪 Development

```bash
pytest -q                       # 72 tests, no desktop required
ruff check aether tests
pytest --cov=aether             # coverage
```

The whole system is built on **injected dependencies** - `Probes`, the LLM
`Transport`, and the `presenter` are all replaceable, so the test suite never
touches your real desktop, clipboard, or display.

## 🗺️ Roadmap

- [x] M1 - package skeleton, config, overlay window
- [x] M2 - context engine with injectable probes
- [x] M3 - Ollama client, defensive parsing, offline fallback
- [x] M4 - risk gate, SQLite vault, audit trail
- [x] M5 - CLI, tests, CI
- [ ] M6 - click-through HUD zones and tray icon
- [ ] M7 - optional cloud LLM backend (Groq/OpenRouter) for users without a GPU
- [ ] M8 - learn from dismissals to tune confidence

## 📄 License

MIT © Aayush Bhatta
