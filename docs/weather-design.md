# Fixed HOME weather design and implementation plan

Date: 2026-09-30. Implements [the supplied ADR](ADR-kari-local-weather-context.md).
The requested implementation authorizes this offline slice, not live activation.

## Design

Use a small Python package with independent configuration, normalization, storage,
worker, and CLI modules. Keep the existing Python validator and all 1.0 artifacts.
The weather worker is a separate supervised process, so a network failure cannot
stall future BLE/VeSync workers. SQLite WAL is the shared durable boundary.

Alternatives considered: an all-in-one daemon would couple worker failures;
a broker or time-series service would add unsupported infrastructure. Separate
processes and SQLite meet this slice's isolation and history requirements.

Normalize only the five requested current variables. Discard coordinates and raw
provider fields before persistence. Require response timing and units; preserve
valid siblings. Outdoor plausibility limits mark suspect evidence, physical
impossibilities suppress it. Never infer observations or model-run identity.

Serialize sample lookup, evidence comparison, revisions, and poll recording in
BEGIN IMMEDIATE transactions. Keep full immutable event JSON plus indexed weather
details and sample heads. Random sample IDs remain stable across restarts.
An acquisition ID makes buffered retries idempotent. All new events are protected
integration-pending history. A bounded local outbox supports later exact-version
enrollment and matching durable acknowledgements, without a network publisher.

Queries select highest revisions after applying the as-of collection cutoff.
Retention removes complete old chains only when all revisions are acknowledged;
offline export alone never authorizes deletion. Backup uses SQLite's backup API.

Configuration v2 remains YAML. Disabled weather needs no coordinates. Enabled
weather requires protected coordinate-file references, explicit local approval
bound to the epoch/coordinates, eligible-use acknowledgement, and provider timing
verification. Reject live export activation in this release. Epoch bindings in
protected storage prevent accidentally moving existing history.

Use monotonic scheduling, bounded jitter, one request at a time, HTTP backoff,
Retry-After, and suspension on configuration/authentication failures. Store poll
outcomes separately from evidence. Health uses weather-specific thresholds and
two consecutive current valid polls for recovery. Storage failure emits fixed
sanitized messages, buffers at most 100 acquisitions, and counts dropped groups.

## Plan and finishing checklist

- [x] Contract: write acceptance tests, run them against the missing 1.1 support,
  add `schemas/kari-event-v1.1.schema.json`, semantic dispatch in
  `scripts/validate_contract.py`, and synthetic `examples/weather-events.jsonl`.
- [x] Runtime: test disabled configuration, malformed provider values, response
  intervals and privacy; implement `kari/config.py` and `kari/weather.py`.
- [x] Durability: test duplicate polls, A-B-A corrections, as-of selection,
  concurrent writers, restart, migrations, retention, backup and acknowledgements;
  implement `kari/store.py` and versioned migration SQL.
- [x] Isolation: test timeout/rate-limit/backoff, suspension, freshness, recovery,
  bounded buffering and missed schedules; implement `kari/worker.py` and HTTP I/O.
- [x] Operations: add `python -m kari` configuration, collection, health, query,
  export, prune and backup commands; add disabled example and systemd unit.
- [x] Acceptance: run all unittest tests and both JSONL validators, exercise CLI
  against a temporary real SQLite database, inspect privacy and unchanged legacy
  artifacts, update README/PRD/ingestion and operational/pilot documentation.
Delivery: commit affected files, push the feature branch, and open a review PR.
Merging and live activation are outside this delivery.

No hardware collectors, notifications, consumer analysis, deployed service, live
provider validation, or seven-day pilot are claimed. Those remain separate gates.


## Offline acceptance evidence

Verified on Python 3.13.14 with jsonschema 4.26.0 and PyYAML 6.0.3:

```sh
.venv/bin/python -W error::ResourceWarning -m unittest discover -s tests -v
.venv/bin/python scripts/validate_contract.py examples/events.jsonl
.venv/bin/python scripts/validate_contract.py examples/weather-events.jsonl
.venv/bin/python -m kari --config examples/config-v2.yaml validate-config
.venv/bin/python -m compileall -q kari scripts tests
git diff --check
```

Result: 52 tests passed; 8 original and 1 synthetic weather example validated.
Configuration validation reports weather and publishing disabled. The original
1.0 event schema, acknowledgement schema, and event fixture are byte-for-byte
unchanged from the base commit. The CLI integration test uses a real temporary
SQLite database for import, duplicate suppression, query, export, backup and
protected pruning. A subprocess test exits abruptly during a transaction, then
checks rollback, integrity, and successful collection after restart.

An independent read-only review found health-state persistence and clock rollback
issues; regression tests reproduce them and the fixes pass. The final review
reported no unresolved important issues. Expected fixed storage-unavailable
messages in fault-injection tests are intentional. No live weather request,
service deployment, receiver publishing, or real seven-day pilot was performed.
