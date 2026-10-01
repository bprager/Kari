# ADR Kári fixed home weather acquisition and history

- Status: Proposed for acceptance and implementation
- Date: 2026-09-30 America/Los_Angeles; sources checked 2026-10-01 UTC
- Target: `bprager/Kari`, independently deployable on Odin
- Consumer: Fjölsviðr, formerly Napoleon; repository and deployment renaming are unverified
- Suggested repository destination: `docs/adr/NNNN-fixed-home-weather-context.md`

## Decision

Add an optional, independently scheduled Open-Meteo weather adapter to Kári. Collect outdoor context for a configured fixed HOME location every 15 minutes, retain durable local history, and expose explicitly typed weather events for later indoor/outdoor analysis in Fjölsviðr. Never follow the phone's location. Weather acquisition and storage belong in Kári; cross-source interpretation belongs in Fjölsviðr.

This ADR is an implementation specification, not evidence of deployed functionality. Implement against the repository actually present at handoff. Keep weather collection and live publishing disabled until their separate activation gates pass.

## Context and existing constraints

The inspected [README](https://github.com/bprager/Kari/blob/main/README.md) and [PRD revision 2.0](https://github.com/bprager/Kari/blob/main/PRD.md) describe requirements and a proposed contract, with offline validation tooling. They do not establish a running collector, existing SQLite deployment, or live receiver. Do not assume runtime components already exist or silently expand this task into implementing every unrelated PRD feature.

The [ingestion contract](https://github.com/bprager/Kari/blob/main/docs/ingestion.md) and [1.0.0 schema](https://github.com/bprager/Kari/blob/main/schemas/kari-event-v1.schema.json) strictly allowlist adapters, event types, metrics, and fields. They preserve immutable events, enforce source-time semantics, and gate publishing on receiver agreement. Weather cannot be inserted into those fields as if already supported.

## Scope

Phase 1 includes configuration, acquisition, validation, SQLite persistence, health, offline export/query, fixtures, and deployment documentation. Preserve BLE and VeSync requirements and behavior. Where the runtime is absent, build the minimal shared runtime needed for this weather slice and document remaining baseline work.

Deferred: future forecasts, forecast verification, historical-provider backfills, nearby-station observations, an on-premises outdoor sensor, HVAC control, dashboards, health advice, and automatic personal correlations. Do not create another repository, broker, or time-series service.

## Acquisition and configuration

Use the Open-Meteo `/v1/forecast` current-conditions interface. Its current values are weather-model estimates, not measurements at the home. Request temperature, relative humidity, dew point, wind speed, and shortwave radiation. Request Celsius, metres per second, and UTC explicitly; validate response units. Model selection initially uses the provider's best-match policy. Preserve unknown model/run identity as null rather than inventing it. Provider timestamp and interval meanings must follow the [official API documentation](https://open-meteo.com/en/docs).

| Provider variable | Kári weather metric | Unit | Temporal meaning |
|---|---|---|---|
| `temperature_2m` | `temperature` | `Cel` | Instantaneous model value, 2 m |
| `relative_humidity_2m` | `relative_humidity` | `%` | Instantaneous model value, 2 m |
| `dew_point_2m` | `dew_point` | `Cel` | Instantaneous model value, 2 m |
| `wind_speed_10m` | `wind_speed` | `m/s` | Instantaneous model value, 10 m |
| `shortwave_radiation` | `shortwave_radiation` | `W/m2` | Mean over the backward-looking response interval |

Verify the selected current-response variables and interval semantics with fixtures before activation. Solar interval duration comes from the response; never assume hourly semantics or a 15-minute average because polling happens every 15 minutes. Missing or ambiguous interval metadata suppresses that metric and raises a quality incident.

Add a weather adapter block to configuration v2 without changing existing IDs or requiring weather fields for disabled configurations:

- `enabled: false`; `provider: open_meteo`; `poll_interval_seconds: 900`, supported initial range 900–1800
- Stable opaque `location_id`, separate `location_epoch_id`, and virtual source `device_id`; HOME is a local display label
- Latitude/longitude references to protected local configuration; explicit IANA display timezone
- `request_timeout_seconds: 20`; `max_age_seconds: 3600`; `model_selection: best_match`
- `history_retention_days: 730`; `poll_retention_days: 90`; `raw_response_retention_days: 0`

These are proposed weather defaults, not measured provider limits. Moving HOME or materially changing coordinate precision creates a new location epoch; never rewrite past location assignments. Changing provider or model-selection policy creates a distinct stream. No geocoding, phone tracking, or automatic provider fallback.

Before activation, show which coordinates will be sent to Open-Meteo and obtain the operator's approval. Offer coarser coordinates as an explicit accuracy/privacy choice. Keep requested and returned grid coordinates local and protected; exclude them, request URLs, and reversible coordinate identifiers from exports, logs, exceptions, and fixtures. HOME membership itself remains household information.

The [provider terms and privacy policy](https://open-meteo.com/en/terms) currently restrict the free service to noncommercial use, require attribution under the data licence, and describe API logs that may retain coordinates for 90 days. Document attribution and confirm the deployment's eligible usage before activation; paid service/account setup is a separate decision. Do not enable external calls merely by accepting this ADR.

## Proposed event contract

Introduce exact version `kari.event` **1.1.0**, with a separate `schemas/kari-event-v1.1.schema.json`. This is a proposed negotiated identifier. A minor version does not make strict 1.0.0 consumers compatible. Leave the 1.0.0 schema and fixtures unchanged; dispatch validation and export by exact accepted version.

Add only `event_type: weather_context` and `source.adapter: open_meteo`, with a strict weather payload. Existing observation/device-state semantics remain unchanged. `device_id` identifies a configured virtual weather source here, not a physical instrument. Require non-null opaque location and epoch IDs for weather.

Each event carries one metric. Metrics first created from the same response share `collection_id`. Keep the existing envelope and immutable UUID rules. For modeled weather, `observed_at` is null, `timestamp_basis` is `unknown`, and envelope `freshness_at_collection` is `unknown`: no physical observation time is asserted. Add weather-specific fields:

- `data_kind`: exactly `modeled_current` in this release
- `valid_at`: UTC RFC 3339 time represented by the model value, no later than `collected_at`
- `valid_time_freshness`: `current` if `collected_at - valid_at <= max_age_seconds`, otherwise `stale`; this does not assert model-run recency
- `temporal_support`: `statistic` (`instantaneous` or `mean`), nullable interval boundaries, and positive `nominal_step_seconds`; means require `interval_start < interval_end = valid_at`, instantaneous values require null boundaries
- `provenance`: provider, product, requested model selection, nullable actual model/run identifiers, source variable, and nullable measurement height
- `sample_id`, `revision`, and nullable `supersedes_event_id`, as defined below
- `metric`, finite `value`, canonical `unit`, `quality` (`valid` or `suspect`), and bounded allowlisted `quality_flags`

`collected_at` is receipt time; `persisted_at` is successful event construction/write time. Preserve `collected_at <= persisted_at` and existing clock-health rules. Recompute weather age from `valid_at` when displaying or analyzing it. Receiving or replaying an event never refreshes its represented time.

Synthetic proposed event, intentionally invalid under 1.0.0:

```json
{
  "schema_version": "1.1.0",
  "event_id": "123e4567-e89b-42d3-a456-426614174001",
  "collection_id": "123e4567-e89b-42d3-a456-426614174002",
  "event_type": "weather_context",
  "source": {
    "component": "kari",
    "collector_id": "odin-kari",
    "adapter": "open_meteo",
    "adapter_version": "0.1.0",
    "collector_version": "0.1.0"
  },
  "device_id": "weather-01",
  "location_id": "location-01",
  "observed_at": null,
  "collected_at": "2026-09-30T21:00:05Z",
  "persisted_at": "2026-09-30T21:00:05.100Z",
  "timestamp_basis": "unknown",
  "freshness_at_collection": "unknown",
  "max_age_seconds": 3600,
  "data": {
    "data_kind": "modeled_current",
    "location_epoch_id": "home-epoch-01",
    "metric": "temperature",
    "value": 23.4,
    "unit": "Cel",
    "quality": "valid",
    "quality_flags": [],
    "valid_at": "2026-09-30T21:00:00Z",
    "valid_time_freshness": "current",
    "temporal_support": {
      "statistic": "instantaneous",
      "interval_start": null,
      "interval_end": null,
      "nominal_step_seconds": 900
    },
    "provenance": {
      "provider": "open_meteo",
      "product": "forecast_current",
      "model_selection": "best_match",
      "model_id": null,
      "model_run_id": null,
      "source_variable": "temperature_2m",
      "height_m": 2
    },
    "sample_id": "123e4567-e89b-42d3-a456-426614174003",
    "revision": 1,
    "supersedes_event_id": null
  }
}
```

Actual library/collector versions must replace the illustrative versions. No precision or calibration claim follows from `quality: valid`. Reject nonfinite values, wrong units, humidity outside 0–100%, negative wind/radiation, impossible timestamps, and unknown fields. Configure outdoor-specific plausibility ranges rather than reusing TP350S indoor limits. Omit invalid/missing metrics, retain valid siblings, and never fabricate zero, clamp values, derive missing dew point, or forward-fill data.

## Persistence and revision semantics

Use the PRD's SQLite WAL architecture with versioned, idempotent migrations and parameterized queries. Add weather-detail, poll-status, and sample-revision tables alongside shared events. Index location epoch/metric/valid time, collection time, and sample/revision. Store canonical events locally even when integration is pending.

1. Define a logical sample key as the canonical tuple of location epoch, virtual source, provider/product, model-selection profile, data kind, metric, valid time, and temporal-support fields. Assign one durable random `sample_id` per key; do not export a coordinate-derived hash.
2. Compare the new normalized evidence to the latest stored revision: value, unit, quality/flags, and full provenance. Ignore receipt/persistence times, acquisition IDs, and computed freshness when deciding whether evidence changed.
3. An identical poll creates only a poll-status record. It does not create another metric event or refresh evidence age. Identical values at a different valid time are different samples.
4. Changed evidence at the same key appends revision N+1 with a new event ID and `supersedes_event_id` pointing to revision N. Even A→B→A is three revisions. Preserve prior revisions unchanged.
5. Serialize lookup, comparison, revision allocation, event write, and any eligible outbox enrollment in one transaction. Enforce unique sample/revision and retry concurrent conflicts safely. Restart and duplicate response processing must be idempotent.

Queries default to the greatest stored revision per sample, never network-arrival order. Support an `as_of` collection-time cutoff for analyses of what was available then; do not silently substitute later corrections. Exports include revisions and IDs so consumers can reproduce selection.

Retain normalized weather history for 730 days by default; allow explicit unlimited retention. This weather policy does not alter the PRD's BLE retention. Prune only whole eligible revision chains, preserving deduplication heads until their samples leave retention. Never prune pending/not-yet-enrolled export history. Use the existing outbox caps and catch-up cursor; disk exhaustion must produce a visible incident and counted gaps, not an unlimited-memory promise. Back up and restore SQLite consistently. Raw provider bodies are disabled by default; optional troubleshooting copies must be redacted and bounded.

## Scheduling and failure isolation

Use one supervised weather worker with at most one in-flight request. Poll on a stable cadence with small jitter; polling faster does not create fresher model data. Cache only until the next scheduled request, keyed by protected location/profile; cached responses retain their original acquisition metadata and create no new acquisition.

On timeouts, 408, 429, or 5xx, use exponential backoff with jitter, beginning at 30 seconds and capped at one hour, honoring a longer `Retry-After`. Suspend on authentication/configuration errors until corrected. Do not make up missed history after an outage. Track scheduled/attempted/successful polls, last transport success, latest valid time, missing metrics, and gaps separately.

Weather failure cannot block BLE, VeSync, watchdogs, storage, or export. Reuse sanitized system-adapter health events. Proposed weather warnings: no usable context after 60 minutes, unavailable after three hours, recovery after two valid polls. External weather notifications remain opt-in; never apply BLE's five-minute absence threshold to a 15-minute weather schedule.

## Consumer boundary and later work

Provide date-range JSONL plus a documented weather query projection. Publishing remains off until Fjölsviðr accepts exact version, source authorization, actual endpoint, credentials, household-data retention, and durable acknowledgements. Keep existing retry IDs, conflict detection, and atomic outbox requirements. Do not invent an endpoint or rewrite Napoleon environment-variable names as part of an unverified rename.

Document a simple downstream recipe: join by HOME epoch and UTC hour; deduplicate revisions first; average available instantaneous samples with sample counts/coverage, and time-weight solar interval means. Missing periods remain missing. Assess illustrative weather-leading lags of 0, 1, 3, and 6 hours against indoor temperature, humidity, or PM2.5. Report data coverage, source type, season/time-of-day and HVAC/window/occupancy confounding. This is exploratory association, not causality or individual exposure measurement. Building that analysis is a separate Fjölsviðr task.

Future forecasts require a separate negotiated contract with distinct issue/run time, valid time, lead time, and revision identity, including `issued_at <= collected_at` and `valid_at >= issued_at`. Never relabel a future forecast as an observation after time passes. Physical outdoor sensors remain distinguishable observations with device provenance.

## Implementation sequence and acceptance

1. Inspect the current checkout and any authorized deployment; inventory runtime gaps. Add this ADR and update PRD/ingestion documentation without claiming deployment.
2. Implement exact-version schema dispatch, strict 1.1.0 weather schema, semantic validator, synthetic fixtures, and regressions first.
3. Add disabled configuration, provider normalization, isolated scheduler, migrations, immutable revision storage, health, and offline export/query.
4. Add attribution/privacy documentation and backup, retention, outage, and rollback procedures. Rollback disables weather without deleting history or changing other adapters.
5. Run offline acceptance, then request separately approved live activation. Deliver a PR with commands, test results, remaining gaps, and a seven-day pilot checklist. Do not enable receiver publishing without its agreement.

| Test | Required result |
|---|---|
| Legacy compatibility | Existing 1.0.0 fixtures remain unchanged and pass; 1.0.0 rejects weather; exact-version routing passes valid 1.1.0 |
| Time and units | UTC/DST boundaries, stale/current boundary, solar intervals, units, nulls, and malformed values validate correctly; future valid times rejected |
| Deduplication | Repeated polls add no samples; changed valid time adds a sample; correction appends a revision; A→B→A and concurrent polls pass |
| Durability | Crash/restart, lost acknowledgement, export replay, and migration rerun preserve IDs, history, and one logical revision chain |
| Isolation | Timeout, rate limit, bad response, and internet loss leave other workers responsive and expose honest freshness/gaps |
| Privacy | No coordinates, URLs, identifiers, credentials, or unredacted responses leak through exports/logs/errors; disabled adapter makes no calls |
| Retention | Protected backlog survives pruning; complete eligible chains expire; backup restoration and rollback preserve existing data |
| Pilot | Seven days report scheduled/attempted/successful polls, coverage, stale/invalid values, revisions, outages, recovery, and storage growth |

The decision trades local-measurement accuracy and offline availability for a low-cost, hardware-free source of outdoor context. Explicit modeled-data labels, local history, and an optional later outdoor sensor keep that limitation visible.
