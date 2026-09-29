# Changelog

All notable changes to AETHER. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[semver](https://semver.org/).

## [0.2.0]

### Added

- Decisions on the card: `d` dismiss, `s` snooze ("never show me this again"),
  `a` mark useful, `q` quit. Decisions are recorded and, for snoozes, block the
  fingerprint for `dismiss_snooze_s`.
- Snooze storage in the vault, with expiry and pruning.
- `aether --stats`: counters by risk and source, plus a dismissal rate that
  tells you when to raise `cooldown_s` or switch suggestions off entirely.
- `aether --clear` and `aether --clear --clear-older-than N`, so history is
  genuinely forgettable.
- `history_retention_days`: history now expires on its own.
- `dwell_reminder_s` and app-dwell tracking, so AETHER can notice a long
  single-app session.
- New deterministic rules for errors, meetings, unsaved documents and dwell.
  The editor's `*` marker is now treated as signal rather than title noise.
- No-op on an unchanged context, which avoids a pointless model round trip.
- Withheld suggestions are written to the audit trail, so `high`-risk
  suppression is provable rather than silent.
- `allow_medium_risk` to switch `medium` hints off entirely.

### Fixed

- `aether --once` hung forever: it posted to a pump that only runs inside
  `Overlay.run()`. Single-shot now renders on the UI thread and waits against a
  deadline.
- `max_suggestions_per_hour = 0` meant *unlimited* instead of the documented
  *disabled*. `-1` is now the explicit "no cap".
- Config values from a JSON file were not coerced, so `"cooldown_s": "900"`
  could reach the runtime as a string. Every field is now coerced and clamped,
  and an unknown config key is reported instead of ignored.
- `db_path` and `config_dir` can be set from the config file.
- `--doctor` no longer claims "Ready" when the model is unreachable, and no
  longer inverts the auto-exec line.
- A corrupt audit row no longer crashes `--history`.

## [0.1.0]

- Initial release: context engine, redaction, Ollama client, risk gate, SQLite
  vault, overlay, CLI.