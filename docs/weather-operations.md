# Fixed HOME weather operations

This release implements the offline weather slice of the [ADR](ADR-kari-local-weather-context.md).
It has not been deployed on Odin or tested against an approved HOME request.
Collection and publishing are disabled in the example configuration. BLE, VeSync,
notifications, general legacy database migration, and a receiver remain separate work.

## Install and validate locally

Use Python 3.11+ and run commands from the checkout:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/validate_contract.py examples/events.jsonl
.venv/bin/python scripts/validate_contract.py examples/weather-events.jsonl
.venv/bin/python -m kari --config examples/config-v2.yaml validate-config
```

Copy `examples/config-v2.yaml` to a protected local file. For an offline trial,
change only `collector.database_path` to a writable test location; leave weather
and export disabled. The synthetic response contains no HOME/grid coordinates.

```sh
umask 077
.venv/bin/python -m kari --config config.local.yaml import-weather \
  --input tests/fixtures/open_meteo_current.json --collected-at 2026-09-30T21:00:05Z
.venv/bin/python -m kari --config config.local.yaml query-weather \
  --start 2026-09-30T00:00:00Z --end 2026-10-01T00:00:00Z
.venv/bin/python -m kari --config config.local.yaml export-weather \
  --schema-version 1.1.0 --start 2026-09-30T00:00:00Z --end 2026-10-01T00:00:00Z > weather.jsonl
.venv/bin/python scripts/validate_contract.py weather.jsonl
.venv/bin/python -m kari --config config.local.yaml health
```

Do not import synthetic data into a production database. `import-weather` is
explicitly offline; it neither fetches data nor verifies provider behavior. A
reused `--collection-id` is an idempotent acquisition retry, even if the input
changes. A new acquisition ID is required for a genuine new response.

## Collection activation gate

Before any real request:

1. Confirm this installation is eligible for the selected provider service. The
   free hosted service is for noncommercial use; paid account setup is separate.
2. Display the exact coordinates locally to the operator, explain that they go to
   Open-Meteo, and obtain explicit approval. Offer rounded/coarser coordinates;
   explain the accuracy tradeoff. Never paste coordinates into repository files,
   exported evidence, logs, review comments, or remote diagnostic tools.
3. Set opaque location, location-epoch, and virtual-device IDs. HOME is a local
   display label. A move or material precision change requires a new epoch.
4. Create the protected coordinate JSON file, owned by the service user with
   mode `0600`. Its exact fields are `latitude`, `longitude`, `approved_latitude`,
   `approved_longitude`, `approved_epoch_id`, and `coordinate_sharing_approved`.
   Both approved coordinates must match the proposed coordinates, the approved
   epoch must match configuration, and approval must be the JSON boolean `true`.
   There is no automatic approval command. The database also binds an epoch to
   its original location and coordinates, so changing approval does not rewrite history.
5. Review the official current-variable and interval documentation and synthetic
   fixture tests. Record this verification locally and set
   `current_semantics_verified: true`; set `eligible_use_confirmed: true` only
   after completing step 1. Set `enabled: true` only after approval in step 2.
6. Run `validate-config`. Then perform an approved `run-weather --once`, inspect
   metric units/times/intervals and quality flags, and compare with a redacted
   real-response fixture before starting unattended collection. Do not retain
   the raw response. Restore `enabled: false` if the response does not match.

The default poll interval is 900 seconds (supported 900–1800), request timeout 20
seconds, and represented-time age limit 3600 seconds. These are project defaults,
not measured provider quotas. Only `best_match` is supported. Unknown actual
model/run IDs stay null. Current response time is requested as UTC Unix seconds;
exported times are UTC RFC3339. Solar interval length comes from `current.interval`.
If interval metadata is invalid, all metrics are suppressed because even an
instantaneous event requires an honest nominal step; a quality incident records this.
Missing metrics and wrong units suppress only the affected metric otherwise.

[Open-Meteo API documentation](https://open-meteo.com/en/docs) states that current
conditions use weather models and that `interval` describes backward-looking
aggregates. [Provider terms/privacy](https://open-meteo.com/en/terms) describe
noncommercial free-service eligibility and API logs including coordinates retained
for up to 90 days. Checked 2026-10-01 UTC. Recheck before activation.

Attribution for displays, derived products, and shared exports: **Weather data by
[Open-Meteo](https://open-meteo.com/), licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Processed by Kári;
modeled estimates, not a measurement at HOME.** Carry this attribution alongside
JSONL exports; the strict event contract does not have an attribution field.

## Supervision and outages

The supplied `deploy/kari-weather.service` is an installation template, not an
enabled service. Provision an unprivileged `kari` account, a protected checkout at
`/opt/kari`, its virtual environment, and `/etc/kari/config.local.yaml` plus the
coordinate file owned by `kari`. Keep `/var/lib/kari` mode `0700`. Only after the
activation gate, install and start this service through normal Odin operations.
This does not start or change OpenClaw services, whose owner remains `clawdbot`.

`run-weather` holds an exclusive local file lock, makes one request at a time, and
uses a monotonic schedule with up to ten seconds of positive jitter. Retries begin
at 30 seconds and cap at one hour; a longer Retry-After is honored. Redirects are
not followed. Configuration/authentication errors suspend requests. Correct the
configuration, stop the worker, run `resume-weather`, then restart. An unchanged
suspension survives restarts. No cached response is re-labeled as a new acquisition.
Missed cadence slots are counted; no synthetic backfill is generated.

Weather runs independently from other future adapters. Poll status distinguishes
scheduled slots, attempts (including retries), transport success, usable metrics,
latest represented time, missing metrics, and gaps. No usable context for an hour
raises suspect status; three hours raises unavailable. Two consecutive current
valid polls recover an availability incident. Fresh transport does not imply
fresh weather. Health events use the system adapter and sanitized fixed messages.
There are no external weather notifications in this release.

A storage outage produces a fixed journal message and buffers at most 100
normalized acquisition groups. Overflow is counted and persisted on recovery;
old acquisition IDs and receipt times survive buffer replay. This buffer is RAM,
so process/host loss can also lose buffered data. SQLite transactions survive
restart; uncompleted poll records expose interrupted acquisitions. Monitor disk
space and make backups. There is no promise of lossless unlimited outage buffering.

## History, query, and retention

`query-weather` selects the greatest revision per logical sample. Use `--as-of`
with a UTC collection-time cutoff to reproduce what was known then. `--start` is
inclusive and `--end` exclusive on represented valid time. `--metric` and `--epoch`
filter before selection. The projection includes:

- immutable event/collection/sample IDs, revision and predecessor;
- opaque device/location/epoch IDs, valid and collection time;
- metric/value/unit, quality/flags, temporal support and modeled provenance;
- recalculated `age_seconds` and `freshness_now` at `--at` (default current UTC).

A negative age in a historical display is unknown, never current. Original
`valid_time_freshness` remains the collection-time fact. Receiving, replaying, or
exporting evidence does not refresh it.

`export-weather` includes every revision and preserves event IDs. It requires an
exact `--schema-version`; accepting 1.0.0 exports no weather, and no downgrade is
invented. Export is repeatable and does not acknowledge delivery.

Weather history defaults to 730 days, poll records to 90 days. Set
`history_retention_days: null` for unlimited weather history. `prune` uses these
settings and removes entire sample chains only after every revision is durably
acknowledged and the entire chain is old enough. Pending, rejected, and
not-yet-enrolled events are protected. Therefore the default integration-pending
installation retains all canonical history even beyond 730 days. BLE retention is
unchanged. Raw-body storage is unsupported and values other than zero for
`raw_response_retention_days` are rejected.

The local outbox API supports exact-version enrollment, 100,000-event / 256-MiB
caps, a durable catch-up cursor, and matching durable acknowledgements. It is
exercised offline only. Publishing is unavailable and `export.enabled: true` is
rejected. Receiver agreement must still cover source authorization, endpoint,
credentials, household retention, exact event version, acknowledgement version,
conflict quarantine, retries and atomic receipt before adding a network exporter.
No Napoleon environment-variable names were renamed.

## Backup, restore, rollback

```sh
.venv/bin/python -m kari --config config.local.yaml backup --destination /protected/path/new-backup.sqlite3
.venv/bin/python -m kari --config config.local.yaml prune
```

Backup uses SQLite's consistent backup API, checks integrity, creates mode `0600`,
and refuses overwrite. Store backups like household data: they include local
coordinates and epoch assignments. To restore, stop the worker, preserve the
current database together with its WAL/SHM sidecars, and restore the backup into a
new protected directory. Point configuration there, validate it, query/export and
compare IDs/counts before restarting. Never copy only the live SQLite main file.
Migrations are transactional and idempotent; a newer unknown database version is
rejected. No preexisting production schema has been inspected or migrated.

Rollback: stop the weather service and set `adapters.weather.enabled: false`.
Keep the database and backup. The 1.0 schema and fixtures remain unchanged, and
other adapter requirements are unaffected. Do not downgrade a weather database
in place or delete history as part of rollback.

## Seven-day pilot (separate approved live work)

- Record baseline database size, configuration revision, provider terms review,
  coordinate approval, clock synchronization, and exact source/epoch IDs locally.
- Each day save protected `health` output and a date-range JSONL export; validate
  it with the contract tool. Record database/WAL growth and backup completion.
- Report scheduled/attempted/successful polls, transport failures, missing/invalid
  metrics, current/stale/suspect evidence, valid-time coverage per metric,
  revisions, availability incidents, and recovery polls. Counts are not a
  substitute for represented-time coverage.
- Exercise an approved network outage and restart; check backoff, counted gaps,
  two-poll recovery, stable IDs, and continued responsiveness of other workers.
- Restore a backup in a separate directory and compare exports. Confirm no
  coordinates/URLs/secrets appear in shared exports or operational messages.
- Review actual provider semantics/units and coverage after seven days. Keep live
  receiver publishing off until its separate agreement and acceptance tests pass.

## Downstream analysis recipe (not implemented here)

Select revisions first (and an as-of cutoff if needed), then join HOME epochs by
UTC hour. Average available instantaneous values while reporting sample counts
and coverage; time-weight solar interval means by overlap. Leave missing periods
missing. Explore weather-leading lags of 0, 1, 3 and 6 hours against indoor
temperature, humidity or PM2.5. Report modeled-source limitations, season and
hour-of-day effects, and HVAC/window/occupancy confounding. This is exploratory
association, not causality or personal exposure. Future forecasts need a distinct
issue/run/lead-time contract; physical outdoor sensors remain observations.
