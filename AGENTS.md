# Host placement and durable preferences

- Do not run, enable, or use Bluetooth services on `odin`; its hardware is unsuitable.
- Run Bluetooth/BLE services on `mimir` instead.
- Non-Bluetooth weather collection may run on `odin`.
- Keep OpenClaw gateway/node services owned by `clawdbot`, never `bernd`.

The Bluetooth rule was explicitly supplied by the user on 2026-09-30 and is
stored globally in Memgraph as `codex:preference:bluetooth-mimir-only`.
The synchronized local mirror is [docs/preferences.json](docs/preferences.json).
