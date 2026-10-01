# Kári Environmental Collector

## Product Requirements Document

| Field | Value |
|---|---|
| Project | Kári |
| Repository | [`bprager/Kari`](https://github.com/bprager/Kari) |
| Status | Expanded implementation requirements; ingestion contract proposed, not deployed |
| PRD revision | 2.0, 2026-09-12 |
| Contract | `kari.event` 1.0.0, JSON Schema Draft 2020-12 |
| Primary host | Odin, Ubuntu |
| Initial sensor | TempPro/ThermoPro TP350, advertised as TP350S |
| Additional source | Levoit Core 300S via VeSync; second purifier model to be confirmed |

## 1. Summary

Kári is an independently deployable environmental collection service on Odin. Its first adapter passively receives TP350S BLE advertisements through the maintained Python [`Bluetooth-Devices/thermopro-ble`](https://github.com/Bluetooth-Devices/thermopro-ble) parser. A second, independently scheduled adapter reads Levoit purifier telemetry through `pyvesync` and the VeSync cloud. Both feed shared validation, local persistence, health monitoring, and a versioned export contract.

This revision expands the original BLE PRD, preserving its sampling, battery safeguards, notifications, and operations requirements. The inspected GitHub main branch contained no collector code or previous ingestion schema. Requirements and examples here do not establish a running service or an accepted Napoleon endpoint. The original PRD remains the baseline for BLE behavior; concrete device identifiers and account details belong in local configuration.

Kári also monitors sensor and collector health. It sends email and SMS notifications when a sensor remains unavailable beyond configured thresholds, when a trustworthy low-battery measurement is available, and when service is restored.

The name Kári comes from the Norse personification of wind and air, fitting a service that quietly observes the home's atmosphere.

## 2. Problem

The vendor application makes current conditions and historical charts available only within its own interface. Napoleon needs locally retained, structured environmental data for correlation and monitoring. BLE collection works without a vendor cloud. VeSync collection explicitly depends on a vendor cloud; Kári must expose that dependency and its outages rather than present cached data as current.

The TP350S broadcasts temperature and humidity passively every few seconds. The original BLE PRD reported successful discovery and decoding on Odin (historical evidence, not revalidated in this revision):

| Attribute | Observed value |
|---|---|
| Advertised name | `TP350S` |
| Address | `configured locally` |
| Decoder | `tp350s-adv` |
| Example temperature | `32.6 C` |
| Example humidity | `41% RH` |
| Example RSSI | `-41 dBm` |

## 3. Goals

1. Capture TP350S BLE advertisements continuously without pairing or maintaining a GATT connection.
2. Decode temperature and humidity through `thermopro-ble` rather than maintaining an unnecessary proprietary decoder.
3. Store timestamp, sensor identity, temperature, humidity, and RSSI locally.
4. Detect prolonged sensor unavailability reliably and distinguish it from collector or Bluetooth adapter failure.
5. Notify Bernd by email and SMS according to a configurable escalation policy.
6. Produce recovery notifications and prevent duplicate alert storms.
7. Run unattended on Odin as a hardened `systemd` service.
8. Expose versioned observation, purifier-state, and health events through local JSONL export and a proposed Napoleon ingestion contract.
9. Reuse supported Levoit hardware for numerical PM2.5 telemetry without adding a new repository or requiring Home Assistant.
10. Distinguish source timestamps, collection timestamps, quality, and freshness for every event.
11. Isolate BLE operation from VeSync, notification, and export failures.

## 4. Non-goals

1. Pairing with the sensor or changing its firmware.
2. Writing to BLE characteristics, including Nordic DFU services.
3. Reproducing the complete TempPro mobile application.
4. Retrieving historical readings recorded before Kári began collecting.
5. Claiming that an unavailable TP350S has definitely exhausted its battery. Loss of advertisements can also mean distance, interference, removal, or hardware failure.
6. Building a dashboard in the first release.
7. Controlling purifier power, fan speed, schedules, child lock, firmware, or account settings through this integration.
8. Treating a purifier as a certified smoke/CO alarm, a calibrated exposure instrument, or a source of CO2, radon, or VOC measurements.
9. Inventing a Napoleon endpoint, registering a capability automatically, or implementing autonomous extension of other projects.
10. Implementing future sensor types in this documentation/schema change.

## 5. Key product decision

### Passive BLE and read-only VeSync collection

Kári must listen for advertisements and must not connect to the TP350S during normal operation. Passive collection:

- permits the TempPro mobile application to continue working;
- supports multiple listeners;
- avoids device locking and connection recovery problems;
- provides every measurement required for the first release.

### Battery-state limitation

The maintained Python parser derives a coarse TP3x battery value from advertisement bits, but its source states that this behavior was verified on TP357S hardware. It is not verified for TP350S. Another TP350S-specific implementation reports that the model does not transmit a dependable battery state.

Therefore:

- TP350S battery percentage must be disabled or marked `unverified` by default.
- Kári must not send a definitive low-battery message based on unvalidated TP350S battery bits.
- Prolonged absence is the reliable operational proxy for a dead battery, but the alert must say that battery depletion is one possible cause.
- Direct battery alerts may be enabled per sensor only after validation against controlled battery tests or authoritative protocol documentation.

## 6. Users and stakeholders

| Role | Need |
|---|---|
| Bernd | Reliable environmental history and actionable failure alerts |
| Napoleon | Structured readings and health events for later analysis |
| Operator | Simple installation, diagnostics, upgrades, and recovery on Odin |

## 7. Functional requirements

### FR-1: Sensor discovery and identity

1. The BLE adapter shall identify the initial sensor by configured BLE address and expected advertised name.
2. The initial configuration shall use the previously identified address from local configuration and name prefix `TP350S`; repository examples must not contain live hardware addresses.
3. Configuration shall support multiple sensors without code changes.
4. Unknown BLE devices shall be ignored.
5. An explicit discovery command shall list compatible ThermoPro devices and their RSSI.

### FR-2: BLE collection

1. The collector shall use the Python `thermopro-ble` package and its advertisement parser.
2. The collector shall use `bleak` and BlueZ through the library-supported integration path.
3. Each accepted internal BLE acquisition shall contain the following local metadata (hardware address/name are excluded from canonical exports):
   - UTC observation timestamp;
   - stable configured sensor ID;
   - BLE address;
   - advertised name;
   - temperature in degrees Celsius;
   - relative humidity percentage;
   - RSSI in dBm;
   - parser and application version metadata.
4. Values outside configured plausible ranges shall be rejected and logged:
   - temperature default: `-20.0` through `60.0 C`;
   - humidity default: `1` through `99% RH`;
   - RSSI default: `-120` through `0 dBm`.
5. Rejected observations shall not update the sensor's healthy last-seen state.
6. The process shall reconnect to BlueZ and resume scanning after transient adapter or D-Bus errors.

### FR-3: Storage

1. SQLite shall be the default first-release store, using WAL mode and parameterized statements.
2. The database location shall be configurable, with a default under `/var/lib/kari/`.
3. For BLE, Kári shall persist one acquisition group per configured interval, defaulting to 60 seconds, using the newest valid advertisement within the interval. The group yields separate canonical metric events.
4. Kári shall update an in-memory last-seen timestamp for every valid advertisement, even when the observation is not selected for persistence.
5. New canonical events shall use UTC RFC 3339 timestamps ending in `Z`, consistently across all tables and exports. See the ingestion contract for source versus collection time.
6. Database migrations shall be versioned and applied idempotently.
7. Default retention shall be unlimited because one reading per minute is modest. A configurable retention job shall be available.
8. CSV and JSONL export shall be supported by date range and sensor ID.

### FR-4: BLE availability monitoring

Kári shall maintain separate states for each BLE sensor:

| State | Default condition | External action |
|---|---|---|
| `STARTING` | Service has not yet received a valid reading | None during startup grace period |
| `HEALTHY` | Recent valid reading received | None |
| `SUSPECT` | No valid reading for 5 minutes | Log and retry, no external notification |
| `UNAVAILABLE` | No valid reading for 30 minutes | Send email |
| `CRITICAL` | No valid reading for 2 hours | Send SMS and escalation email |
| `RECOVERED` | Three consecutive valid readings after an alert | Send recovery email and SMS if SMS was previously sent |

All durations and recovery counts shall be configurable.

Before attributing an outage to the sensor, Kári shall verify:

1. the collector process is running;
2. BlueZ is active;
3. the configured Bluetooth adapter is powered and not blocked;
4. BLE scanning is producing observations from any devices, when such devices are available;
5. the last database write or health checkpoint succeeded.

If the collector, adapter, or database is unhealthy, the alert shall identify an infrastructure failure rather than a sensor failure.

### FR-5: Battery monitoring

1. A sensor configuration shall declare battery support as `unsupported`, `unverified`, or `verified`.
2. TP350S shall default to `unverified`.
3. Unverified battery values may be logged for diagnostic comparison but shall not trigger notifications or be presented as fact.
4. For a sensor with verified battery telemetry:
   - send an email at or below 20%;
   - send an SMS and email at or below 10%;
   - require at least three consecutive low readings before alerting;
   - apply hysteresis, default 5 percentage points, before declaring recovery.
5. When TP350S becomes unavailable, notification text shall say: "Kári has not received the sensor for {duration}. Possible causes include depleted battery, distance, interference, or device failure."

### FR-6: Notifications

1. Email shall be delivered through a configurable SMTP provider or an existing Napoleon notification adapter.
2. SMS shall be delivered through a provider adapter, initially supporting Twilio or another selected SMS gateway.
3. Provider credentials shall be read from environment variables or the host's secret store, never committed to source control.
4. BLE sensor alert messages shall include:
   - sensor name and ID;
   - severity;
   - last valid reading time;
   - outage duration;
   - last temperature and humidity;
   - last RSSI;
   - likely causes and a concise next action.
5. Kári shall deduplicate alerts by sensor, condition, and escalation level.
6. An unresolved condition shall not generate more than one reminder per configurable interval, default 24 hours.
7. Notification attempts and provider results shall be recorded without storing message credentials.
8. Notification delivery shall retry with exponential backoff and a bounded retry count.
9. A test command shall send clearly labeled test email and SMS messages.

### FR-7: Recovery and maintenance

1. Kári shall resume automatically after host reboot, service restart, Bluetooth restart, or temporary database unavailability.
2. Graceful shutdown shall flush pending samples and close database resources.
3. The service shall support configuration validation without starting collection.
4. Package dependencies shall be pinned through `uv.lock` and updated through a tested maintenance process.
5. The service shall expose a command that reports collector, adapter, database, sensor, and notification-provider health.

### FR-8: Napoleon integration

1. Local SQLite and CSV/JSONL export remain available independently of Napoleon.
2. Canonical events shall validate against [the event schema](schemas/kari-event-v1.schema.json) and [semantic rules](docs/ingestion.md).
3. Exported observations, device state, and health events use separate event types. Environmental data remain time-series evidence; graph summaries may reference event IDs.
4. Live publishing is disabled by default. Its endpoint, authentication, accepted schema version, acknowledgement behavior, and permission to retain household data must be agreed with Napoleon before enabling it.
5. Yggdrasil is the proposed discovery path for that agreement. A missing/stale contract creates an integration-pending status, while local collection continues. No runtime write capability was established in this review.
6. Once publishing is enabled, commit each event and its outbox row atomically; retry with the same event ID and unchanged payload until a durable acknowledgement. See the ingestion contract for duplicate, conflict, and rejection behavior.
7. Export failures shall never stop collection. Persist backlog age/count and bounded disk usage; no silent deletion of unacknowledged events.

### FR-9: VeSync source and identity

1. Use a tested, pinned `pyvesync` release; do not assume library major versions share an API. Home Assistant may provide an alternative configured source, but must not duplicate direct collection of the same device.
2. The owner operates two Levoit purifiers; one photographed model is Core 300S. Confirm each discovered model and supported fields individually before enabling it.
3. Configure devices by stable local `device_id` mapped to a vendor device identifier, not by mutable display name. Store provider identifiers and credentials locally; export only opaque local IDs.
4. Explicit account-scoped discovery is allowed; continuous collection uses only allowlisted device IDs. Do not retain unrelated account devices.
5. Read numerical PM2.5 where exposed; never convert a color/category or fan setting to a concentration.
6. Read optional purifier state: power, mode, speed setting, vendor air-quality category, and estimated filter-life percentage. Unsupported or invalid fields are absent, never synthesized as zero.
7. Do not invoke device-changing methods. Logins, read requests, and token refresh are allowed; purifier operation remains user/vendor controlled.
8. Use a configurable starting poll interval of 120 seconds per device, staggered within an account. This is a Kári default, not a claimed vendor limit. Respect server retry guidance, apply exponential backoff with jitter, and bound concurrency/timeouts. Authentication errors suspend that adapter until reauthentication; never tight-loop logins.
9. Persist each successful poll as an evidence snapshot, retaining unknown measurement time when the API provides none. Equal consecutive values alone do not prove stale data.
10. VeSync network/authentication/rate-limit failures create adapter health events. A healthy account response reporting an individual device offline creates a device-scoped event. Dependent device data become unavailable/unknown; do not send a separate root-cause alert for every device in an account outage.
11. A purifier intentionally disabled in Kári is `disabled`. A vendor power-off state is operational state, not proof of failed collection; only publish sensor values when the model's measurement behavior in that state is verified.

### FR-10: Shared observations and freshness

1. Each observation contains one metric, canonical unit, finite numerical value, stable device/location IDs, provenance, and quality. Temperature/humidity/RSSI are not mandatory for PM2.5 events. Group readings from one acquisition with a shared `collection_id`.
2. `observed_at` is the known evidence time: validated device measurement time, or BLE collector reception time. BLE reception is explicitly identified as reception, not the sensor's internal sampling time.
3. `collected_at` is receipt by Kári; `persisted_at` is construction/storage time of the immutable event. VeSync without a trustworthy source timestamp uses `observed_at: null`, `timestamp_basis: unknown`, and `freshness_at_collection: unknown`.
4. Consumers recompute current freshness from observed time and the event's maximum age. Delivery, replay, and successful polling never reset measurement age.
5. Track data validity, measurement freshness, and transport availability separately. A reachable cloud is not proof of a current measurement.
6. Preserve BLE defaults: 60-second samples; plausible ranges -20 to 60 Celsius, 1 to 99% RH, and -120 to 0 dBm. These model-specific checks are stricter than general wire-schema limits.
7. PM2.5 is nonnegative in `ug/m3`. Do not invent precision, calibration, upper range, or health thresholds. Validate model limits when documented; quarantine invalid values. Retain valid fields from a partially invalid response with a separate data-quality event.
8. Unverified TP350S battery values remain local diagnostics and cannot be exported as verified observations or trigger battery alerts.
9. Optional concentration advisories require configured duration and hysteresis, separate from availability alerts. They are disabled initially; vendor air-quality labels are not official US AQI.
10. Use monotonic time for watchdog durations; use synchronized UTC for exported timestamps. Quarantine impossible time order and emit a clock health event.

### FR-11: Isolation and shared operations

1. BLE, VeSync, notifications, and export run in independently supervised workers with bounded queues. A stalled HTTP request must not block BLE reception or the watchdog.
2. Keep the FR-4 BLE state machine and thresholds. VeSync uses the same 5-minute suspect, 30-minute email, and 2-hour SMS escalation defaults for failed collection, with 120-second startup grace and three consecutive successful valid polls for recovery. Missing source time alone does not constitute a network outage.
3. On startup, restore prior last-seen/incident/delivery state. If a device has never been seen, time absence from monitoring start after the grace period; restart must not reset an existing outage or duplicate notifications.
4. Remember incident/escalation IDs across restart. Recovery notifications apply only to channels that received an alert.
5. Last valid readings in alerts must be labeled with time/freshness. VeSync alerts use last PM2.5 and purifier state when available, rather than mandatory temperature/humidity/RSSI.
6. The local health report includes adapter, storage, notification, export, per-device status, last successful poll, last known measurement, and dependency versions. It must remain available during internet loss.

### FR-12: Configuration and migration

1. Configuration v2 has independent adapter and device sections. VeSync is opt-in, and its missing credentials cannot prevent an enabled BLE adapter from operating.
2. Preserve the original v1 BLE configuration through an explicit, validated migration command. Map old sensor IDs unchanged and split adapter fields from device fields. Never infer a physical room from a display name without operator confirmation.
3. Where a v1 database exists, take a consistent backup and run an idempotent transactional migration. Preserve original readings, UTC times, and sensor IDs; map old database reading IDs to stable new event IDs so migration retries cannot duplicate history.
4. Preserve legacy BLE exports via a documented projection, and add versioned canonical JSONL. Do not reuse `observed_at` for VeSync polling time.
5. No deployed v1 database was inspected. Migration must be exercised against an actual schema or a documented fixture before production use.

## 8. Data model

The wire contract is authoritative for exported events; SQLite tables are an implementation proposal.

| Table | Required contents |
|---|---|
| `device` | Stable device_id, display name, optional location_id, adapter_id, model, locally protected BLE/vendor identifier, enabled flag, capability status, created_at |
| `event` | event_id primary key, collection_id, event_type, source/provenance, device_id nullable for infrastructure events, location snapshot, observed_at nullable, collected_at, persisted_at, timestamp_basis, freshness_at_collection, max_age_seconds, schema_version, canonical payload |
| `observation` | event_id foreign key, metric, value, unit, quality; one row per observation event |
| `device_state` | event_id foreign key, optional power/mode/speed/category/estimated filter-life values |
| `health_event` | event_id foreign key, incident_id, scope, condition_code, status, severity, started_at, resolved_at, sanitized summary |
| `notification_delivery` | Incident, condition, escalation, channel, pending/sent/failed status, attempts, next retry, sanitized provider reference/error; durable uniqueness prevents duplicate escalation |
| `outbox` | event_id unique foreign key, pending/acked/rejected status, attempts, next retry, last acknowledgement/error, configured target |
| `migration_map` | Legacy store identity and reading ID mapped to stable canonical event IDs |

Index events by `(device_id, observed_at)`, `collected_at`, and outbox status/retry time; index observations by metric. Keep room assignment as an event-time snapshot so moving a purifier does not rewrite history. Hardware replacement receives a new device ID.

SQLite WAL, parameterized queries, local backup, retention, CSV/JSONL export, and bounded buffering remain required. A storage outage cannot be durably recorded in the failing database: emit a sanitized journal event and keep a bounded memory queue (100 acquisition groups per device by default). Count and report overflow, then persist the incident and buffered records after recovery. Persisted timestamps reflect the actual later write; original observed/collected times remain unchanged.

## 9. Architecture

Kári shall use a small modular Python architecture:

| Component | Responsibility |
|---|---|
| BLE adapter | Receive BlueZ advertisements through the supported library path |
| ThermoPro decoder | Convert advertisements into sensor measurements |
| VeSync adapter | Read allowlisted purifier telemetry with independent timeout, polling, and backoff |
| Normalizer | Build versioned observation, device-state, and health envelopes with explicit source time |
| Outbox exporter | Deliver immutable events to an agreed Napoleon boundary; disabled until configured |
| Validator | Enforce identity and plausible value rules |
| Sampler | Select the newest valid reading for each persistence interval |
| Repository | Persist readings, events, and delivery records |
| Watchdog | Evaluate sensor, adapter, collector, and database health |
| Alert manager | Apply thresholds, deduplication, escalation, and recovery logic |
| Email adapter | Deliver email notifications |
| SMS adapter | Deliver SMS notifications |
| CLI | Discovery, validation, health, export, and test notifications |

No large message broker, time-series database, container platform, or LLM is required for the first release.

## 10. Configuration

Configuration v2 uses YAML with secret references. This is a proposed configuration contract, not an implemented parser. Placeholder IDs and locations are illustrative.

```yaml
version: 2
collector:
  id: odin-kari
  startup_grace_seconds: 120
  database_path: /var/lib/kari/kari.sqlite3
  buffer_groups_per_device: 100
adapters:
  thermopro:
    type: thermopro_ble
    enabled: true
    interface: hci0
    sample_interval_seconds: 60
  levoit:
    type: vesync
    enabled: false
    username_env: KARI_VESYNC_USERNAME
    password_env: KARI_VESYNC_PASSWORD
    region: US
    poll_interval_seconds: 120
    request_timeout_seconds: 20
devices:
  - id: thermopro-01
    adapter_id: thermopro
    model: TP350S
    address_env: KARI_TP350S_ADDRESS
    advertised_name_prefix: TP350S
    location_id: null
    battery_capability: unverified
    measurement_max_age_seconds: 300
  - id: purifier-01
    adapter_id: levoit
    model: Core 300S
    vendor_device_id_env: KARI_PURIFIER_01_ID
    location_id: null
    measurement_max_age_seconds: 600
availability:
  suspect_after_seconds: 300
  unavailable_after_seconds: 1800
  critical_after_seconds: 7200
  recovery_count: 3
notifications:
  reminder_interval_seconds: 86400
  email:
    enabled: false
    recipient_env: KARI_EMAIL_RECIPIENT
    smtp_host_env: KARI_SMTP_HOST
    username_env: KARI_SMTP_USERNAME
    password_env: KARI_SMTP_PASSWORD
  sms:
    enabled: false
    provider: twilio
    recipient_env: KARI_SMS_RECIPIENT
    account_sid_env: KARI_TWILIO_ACCOUNT_SID
    auth_token_env: KARI_TWILIO_AUTH_TOKEN
    sender_env: KARI_TWILIO_FROM
export:
  enabled: false
  schema_version: 1.0.0
  endpoint_env: KARI_NAPOLEON_ENDPOINT
  token_env: KARI_NAPOLEON_TOKEN
  max_pending_events: 100000
  max_backlog_bytes: 268435456
```

Initial device capability reports must confirm the enabled model and fields. Add the second purifier only after discovery and identity confirmation. Operator configuration supplies actual room assignments. An in-place v1 migration retains prior IDs rather than adopting these example IDs.

Invalid core configuration fails validation. Disabled adapters/channels do not require secrets. Missing secrets for enabled VeSync or notification channels mark those components unavailable with a conspicuous health event while BLE continues; a configurable strict startup mode may fail instead. Production acceptance requires configured, tested email and SMS channels even though repository examples disable external sends.

When an outbox limit is reached, halt new outbox enrollment, continue local event storage, raise an export-capacity incident, and retain a durable catch-up cursor. Resume enrollment from local events when space returns. Retention cannot remove unacknowledged or not-yet-enrolled events. Near-full disk escalates separately; collection buffering remains bounded and any loss must be counted.

## 11. Operational requirements

1. Run under a dedicated unprivileged `kari` account.
2. Grant only the permissions required to access BlueZ, the data directory, logs, and necessary outbound VeSync, notification, and explicitly configured ingestion endpoints.
3. Run as `kari.service` under `systemd` with automatic restart and bounded backoff.
4. Log structured JSON to the journal without secrets or complete provider responses.
5. Provide the following commands:
   - `kari discover --adapter thermopro|levoit`
   - `kari validate-config`
   - `kari health`
   - `kari migrate-config`
   - `kari validate-event <file>` (planned runtime command; the repository validation script is available now)
   - `kari export --from ... --to ... --format csv|jsonl`
   - `kari test-notification --channel email|sms`
6. Report application and dependency versions in health output.
7. Use NTP-synchronized host time and warn when time synchronization is unhealthy.
8. Back up the SQLite database through Napoleon's normal backup process.

## 12. Security and privacy

1. BLE data remain local except configured notifications and explicitly enabled Napoleon export. VeSync telemetry already traverses the vendor cloud; Kári reads it and persists a local copy. Do not claim VeSync is local-only.
2. BLE collection shall be read-only and passive.
3. Secrets shall never appear in configuration committed to Git, logs, exports, or exception traces.
4. Logs shall not record unrelated nearby Bluetooth payloads or device names.
5. The collector shall process only configured sensors after discovery is complete. Restrict permissions on account credentials, token caches, local device mappings, and SQLite backups. Export opaque IDs, not account identifiers, hardware addresses, email/phone destinations, or child location/person records. Room IDs refer only to configured locations; Napoleon performs authorized personal correlation.
6. SMS and email destinations shall be explicitly configured and verified through test notifications.
7. Dependency versions shall be pinned and reviewed before upgrades.

## 13. Reliability and performance

1. Target monthly BLE collection availability: at least 99.5%, excluding Odin downtime. Report raw uptime and excluded downtime separately.
2. At least 95% of configured one-minute sample intervals shall contain a reading while the sensor is healthy and in range.
3. The BLE-only profile shall retain the original target of less than 100 MB resident memory and negligible sustained CPU. Measure and document the additional VeSync overhead in the seven-day trial; do not claim the target has been met without measurement.
4. A malformed advertisement shall not terminate the service.
5. Notification failure shall not stop BLE collection or persistence.
6. Storage failure shall emit a journal health event and preserve a bounded in-memory buffer, default 100 acquisition groups per device, with explicit overflow counters.

## 14. Acceptance criteria

### Collection

- Given the observed TP350S is within range, Kári records temperature, humidity, RSSI, and UTC timestamp for at least 95 of 100 consecutive one-minute intervals.
- Recorded values match the device display within the device's display precision and expected update timing.
- The TempPro mobile application remains usable while Kári is running.

### Availability alerts

- Removing the TP350S battery moves the sensor through `SUSPECT`, `UNAVAILABLE`, and `CRITICAL` at accelerated test thresholds.
- Exactly one email is sent on entry to `UNAVAILABLE`.
- Exactly one SMS and one escalation email are sent on entry to `CRITICAL`.
- Restoring the battery and receiving three valid readings produces the expected recovery notifications.
- Stopping BlueZ generates an adapter failure, not a false sensor battery diagnosis.

### Battery behavior

- TP350S-derived battery values cannot trigger alerts while capability is `unverified`.
- The unavailable alert identifies battery depletion only as a possible cause.

### Operations

- The service starts automatically after an Odin reboot.
- A forced process crash results in automatic restart.
- Configuration validation, health inspection, export, and test notifications operate without editing application code.
- No credentials appear in journal output or exported readings.

### VeSync and contract acceptance

- Confirm model, provider identity, room assignment, and actual exposed fields for each enabled purifier.
- A seven-day trial reports scheduled versus attempted versus successful polls, unknown source times, response latency, outages, invalid fields, and recovery. Target at least 95% successful scheduled polls while the device and vendor service are reachable; separately report overall success including dependency outages.
- Compare persisted values to time-aligned VeSync display values. This checks extraction consistency, not absolute sensor accuracy. No deliberate smoke generation is required.
- A test spy must show zero purifier-changing API calls, including during discovery and authentication recovery.
- Simulate timeout, rate limiting, expired credentials, internet loss, and one-device-offline responses. BLE sampling continues; root-cause incidents are deduplicated.
- Missing source timestamps remain null/unknown. Repeated cloud payloads with unchanged device timestamp never become new measurements; identical values with distinct timestamps remain valid acquisitions.
- Validate all example event types and reject mismatched units, empty payloads, malformed timestamps, nonfinite numbers, unsupported versions, and impossible source/time combinations.
- Simulate exporter restart and duplicate delivery: one durable receiver record per event ID; a conflicting payload is rejected; rejected events remain inspectable. Publishing stays disabled without an agreed receiver contract.
- Source/room mappings and schema migration preserve existing BLE history and survive reruns.

## 15. Implementation phases

### Phase 0: Contract foundation

- Adopt this PRD, event schema, examples, and ingestion semantics.
- Validate the contract offline; agree the receiver boundary before live publishing.
- Preserve BLE deployment/configuration compatibility if an implementation exists outside this repository.

### Phase 1: Proven BLE collection

- Create the Python project using `uv`.
- Integrate and pin `thermopro-ble`.
- Capture the configured TP350S.
- Validate readings against the physical display.
- Persist one-minute samples to SQLite.
- Add discovery, configuration validation, health, and export commands.

### Phase 2: Health and notifications

- Implement the availability state machine.
- Add collector, adapter, and database health checks.
- Add email and SMS adapters.
- Add deduplication, escalation, retries, and recovery notifications.
- Validate with battery-removal and BlueZ-outage tests.

### Phase 3: Production operation

- Add the dedicated service account and `systemd` unit.
- Add structured logging, backup, retention, and dependency-update procedures.
- Run a seven-day stability test.

### Phase 4: VeSync observation

- Add a read-only pyvesync adapter with independent scheduling and credentials.
- Enable individually confirmed devices; run the seven-day observation trial.
- Validate cloud uncertainty, partial responses, outage attribution, and token recovery.

### Phase 5: Napoleon integration

- Agree a versioned ingestion boundary, credentials, and acknowledgements with Napoleon through the available project governance/discovery process.
- Enable durable outbox publishing only after receiver contract tests pass.
- Add correlations or summaries only after sufficient data quality and retention are demonstrated.

## 16. Risks and mitigations

| Risk | Mitigation |
|---|---|
| TP350S battery field is misleading | Disable battery-triggered alerts until independently verified |
| BLE address changes | Support re-discovery and identity confirmation before updating configuration |
| Radio interference causes gaps | Use staged thresholds and RSSI trend context |
| BlueZ failure looks like sensor failure | Check adapter and scanner health before classifying sensor status |
| SMS/email outage hides a failure | Persist delivery state, retry, and expose provider health |
| Excessive repeated alerts | State transitions, deduplication, escalation levels, and reminder limits |
| Parser behavior changes | Pin dependencies and maintain captured advertisement fixtures for regression tests |
| Database growth | One-minute sampling, indexes, optional retention, and export |

## 17. Open decisions before implementation

1. Select the SMS provider. Twilio is the initial recommendation unless Napoleon already has a suitable notification adapter.
2. Select the outbound email account or SMTP relay.
3. Confirm the sensor's physical location and final display name.
4. Decide whether email begins at 30 minutes and SMS at 2 hours, or whether both should use another threshold.
5. Repository placement is resolved: use `bprager/Kari`, independently deployable on Odin.
6. Confirm the second purifier model, VeSync registration, device identifiers, room placement, and exposed source timestamps.
7. Agree Napoleon's ingestion endpoint, supported schema version, authentication, retention, and acknowledgement semantics; no active endpoint is asserted here.
8. Select any PM2.5 advisory policy separately from collection. Confirm source-time semantics against real API responses before declaring cloud measurements current.

## 18. References

- [`Bluetooth-Devices/thermopro-ble`](https://github.com/Bluetooth-Devices/thermopro-ble), maintained Python BLE advertisement parser
- [`parser.py`](https://github.com/Bluetooth-Devices/thermopro-ble/blob/main/src/thermopro_ble/parser.py), TP3x decoding and battery-field implementation
- [Home Assistant ThermoPro integration](https://www.home-assistant.io/integrations/thermopro/), production consumer of the parser

- [pyvesync](https://github.com/webdjoe/pyvesync), supported devices and version-dependent Python API
- [Home Assistant VeSync integration](https://www.home-assistant.io/integrations/vesync/), Core 300S numerical PM2.5 and cloud polling
- [EPA air sensor guidance](https://www.epa.gov/wildfires/using-air-quality-sensors-smoke-what-consider), interpretation and location limits
- [Ingestion contract](docs/ingestion.md), proposed Kári event and delivery semantics

## 19. Revision notes

Revision 2.0 expands the original BLE PRD supplied on 2026-09-12. It retains FR-1 through FR-7, battery validation safeguards, staged email/SMS alerts, SQLite, CLI, systemd, and the seven-day stability requirement. It generalizes the storage/wire model, adds FR-9 through FR-12, and resolves repository placement. Live hardware identifiers are externalized to local configuration. No original receiver wire schema or deployed database was available; this is the first proposed wire version, not a backward-compatible claim about Napoleon.


## Fixed HOME weather implementation addendum — 2026-09-30

The [fixed HOME ADR](docs/ADR-kari-local-weather-context.md) adds an optional
Open-Meteo weather adapter. The repository now includes the minimal weather
runtime: configuration v2 weather parsing, current-model normalization, SQLite
WAL migrations and immutable sample revisions, isolated scheduling, local health,
and offline JSONL export/query. See the [design](docs/weather-design.md) and
[operations guide](docs/weather-operations.md) for exact commands and limits.

The configuration block is `adapters.weather`; the complete disabled example is
[config-v2.yaml](examples/config-v2.yaml). It requires no coordinates when
disabled, uses a fixed HOME epoch rather than phone location, polls every 900
seconds, and retains protected integration-pending history. Existing adapter
blocks and stable device IDs are not migrated or changed by the weather parser.
Weather history defaults to 730 days, distinct from unlimited BLE retention.

Wire version 1.1.0 is a separate exact-version schema; it adds `weather_context`
and the `open_meteo` adapter. Modeled valid time is never an observation timestamp.
Identical polls create poll records only; corrections append immutable revisions.
Coordinates remain local except the separately approved Open-Meteo request.

This addendum does not claim deployed collectors, a seven-day pilot, existing
production database migration, implemented BLE/VeSync collection, external
notifications, or accepted receiver integration. All original BLE/VeSync
requirements remain. Weather collection requires explicit location sharing and
service-eligibility approval; live publishing remains unavailable.
