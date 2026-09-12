# Kári ingestion contract

Status: proposed contract 1.0.0, dated 2026-09-12. This defines Kári's export boundary; it is not evidence of an implemented Napoleon receiver, writable Yggdrasil gateway, or running collector.

## 1. Scope and ownership

Kári collects and validates observations, retains local history, tracks availability, and prepares immutable events. Napoleon owns cross-source interpretation and authorized personal correlation. Yggdrasil is the intended discovery surface for agreed capabilities and contracts. No suitable ingestion contract was returned by the available Yggdrasil search during this revision; an empty result does not establish that no other contract exists.

Local JSONL export is the initial usable interface. Live transport remains disabled until the receiver owner accepts the schema, authentication, permissions, and acknowledgement rules below. The proposed HTTP profile is deliberately independent of an invented route. Configure an actual approved endpoint later.

Artifacts:
- [Event schema](../schemas/kari-event-v1.schema.json)
- [Success acknowledgement schema](../schemas/kari-ack-v1.schema.json)
- [Synthetic events](../examples/events.jsonl)
- [Offline validator](../scripts/validate_contract.py)

## 2. Envelope

Every JSONL line is one complete UTF-8 JSON event, maximum 16 KiB encoded. Reject duplicate JSON keys, NaN, Infinity, unsupported versions, and unknown fields. JSON Schema Draft 2020-12 plus the semantic rules below is required; shape validation alone is insufficient.

| Field | Meaning |
|---|---|
| schema_version | Exact accepted version, initially 1.0.0 |
| event_id | UUID minted once before durable storage; unchanged on every export/retry |
| collection_id | UUID grouping metrics and state from one selected BLE reception or one cloud poll; health evaluations get their own collection ID |
| event_type | observation, device_state, or health_event |
| source | component=kari, stable collector ID, adapter type, collector_version, and exact installed parser/library adapter_version (system events use application version) |
| device_id | Stable opaque local ID; required for observations/state and device health; null for infrastructure-wide events |
| location_id | Optional opaque room ID at collection time; null when unassigned or device_id is null |
| observed_at | Known evidence timestamp or null, never a fabricated cloud measurement timestamp |
| collected_at | Kári receipt time |
| persisted_at | Time the immutable event was prepared for its successful local write |
| timestamp_basis | device, collector_receive, or unknown |
| freshness_at_collection | current, stale, or unknown at collected_at; historical assessment, not delivery-time freshness |
| max_age_seconds | Evidence-age limit from the configured metric/source profile; default BLE 300, VeSync 600 |
| data | Strict event-type payload |

Use UTC RFC 3339 with seconds, optional fractional seconds, and trailing Z. Offset timestamps are normalized before export. UUIDs, opaque IDs, and schema versions must survive restarts. Events contain no BLE address, vendor account/device ID, email address, phone number, precise geographic coordinates, or person identifiers. Those mappings are local. Free text must be sanitized; a schema cannot guarantee redaction.

## 3. Event payloads

### observation

Each event has metric, finite numeric value, unit, and quality (valid or suspect). Valid means the adapter/profile checks passed, not laboratory calibration or a declaration of safe air.

| Metric | Unit | Wire limits | Initial adapter |
|---|---|---|---|
| temperature | Cel | >= -273.15 | thermopro_ble |
| relative_humidity | % | 0 through 100 | thermopro_ble |
| rssi | dBm | -127 through 0 | thermopro_ble |
| pm2_5 | ug/m3 | >= 0 | vesync |
| battery | % | 0 through 100 | thermopro_ble, only with verified per-device capability |

Model profiles apply stricter limits, including TP350S temperature -20 through 60, humidity 1 through 99, and RSSI -120 through 0. Profile validation occurs before event construction. The supplied validation helper enforces adapter/metric compatibility and requires an explicit verified capability for battery events; it does not know real deployment model limits.

Unverified TP350S battery bits remain local diagnostics. Do not export them as factual measurements. Future CO2/radon or other metrics require a new explicitly negotiated schema/profile; v1 must reject unknown metrics.

A PM2.5 event needs no temperature or humidity fields. Missing/invalid/unsupported values are omitted by not generating that metric's event. Never fabricate a zero or forward-fill an observation. Zero PM2.5 is valid when actually reported. A partially invalid acquisition can still emit its valid metrics and a separate data-quality health event.

### device_state

VeSync-only snapshot of optional power, mode, speed_setting, air_quality_category, and filter_life_percent. At least one meaningful field is required. Mode, speed, and category are vendor-reported bounded strings, not inferred measurements. Normalize power to on/off only when explicitly reported.

Filter life is a vendor estimate; filter_life_basis=estimated is mandatory whenever filter_life_percent is present and vice versa. Reject percentages outside 0 through 100. Do not equate filter life with measured filtration effectiveness, or vendor categories with official AQI. Optional fields may be absent.

### health_event

Contains incident_id, scope, condition_code, status, severity, started_at, resolved_at, and sanitized summary. Scope is device, adapter, collector, storage, notification, or export. Recovered events require resolved_at; other statuses require null. A critical status requires critical severity. Incident IDs remain stable through escalation/recovery; each transition has a distinct event ID. State changes are ordered using evidence time, not network arrival order.

The system adapter can emit only health events. A device-scoped event requires device_id. Never label a vendor outage as a proven dead sensor battery. Device reachability, data freshness, and measurement quality are distinct signals.

## 4. Time and freshness semantics

1. BLE observation time is host reception time, with timestamp_basis=collector_receive and observed_at=collected_at. This does not claim the device's internal sampling time.
2. A cloud value without a documented trustworthy measurement timestamp uses observed_at=null, timestamp_basis=unknown, and freshness_at_collection=unknown, even if the HTTP call succeeded.
3. If a validated cloud measurement time exists, preserve it as observed_at with timestamp_basis=device. Cloud events cannot use collector_receive.
4. observed_at <= collected_at <= persisted_at. Reject/quarantine impossible order instead of replacing the source time with now. Health started_at and resolved_at cannot exceed collected_at; resolved_at cannot precede started_at.
5. For known time, current means collected_at - observed_at <= max_age_seconds; otherwise stale. Unknown time remains unknown.
6. Consumers recompute freshness at evaluation time from observed_at. A delayed event can be current-at-collection yet stale on arrival. Retries retain all original timestamps.
7. A successful poll updates transport health; it does not reset source measurement age. Unknown cloud measurement age may support clearly qualified trend displays, but is not trusted as a current safety signal.
8. Identical values with different acquisition times are not automatically stale. Identical cloud source timestamps represent repeated snapshots of one measurement: preserve poll/availability history, but downstream analytics must not count them as independent physical measurements. If source time is unknown, independent measurement count is also unknown.
9. Maintain monotonic watchdog clocks and UTC wall clocks. Receiver runtime checks must reject/quarantine timestamps beyond its configured clock-skew tolerance (initial 120 seconds); this wall-clock check is not part of static fixture validation.

## 5. Delivery and acknowledgement

Initial transport: append-only local JSONL export or a read-only query adapter. CSV is a convenience projection and does not carry the full event semantics.

Proposed live profile after agreement:
- HTTPS POST one event as application/json to the configured endpoint. No batch API is assumed.
- Use an approved secret-store-backed credential; never put tokens in URLs, schema examples, or logs.
- Require source authorization bound to the authenticated producer/device allowlist; do not trust payload source names alone.
- Persist event plus outbox row atomically. Freeze the event ID and payload before sending.
- Receiver durably commits the event and deduplication record in one transaction, then returns HTTP 200 or 201 with the success acknowledgement schema. accepted means newly stored; duplicate means already stored identically.
- Acknowledgement event_id must match the request. stored_at is receiver storage time, not measurement time. An HTTP 2xx without a valid matching durable acknowledgement, including 202, does not dequeue the event.
- The receiver deduplicates on event_id and compares parsed JSON values including all fields, ignoring only object key order and insignificant JSON formatting. Numbers compare by numerical value. Same ID with a different payload returns HTTP 409 and is quarantined as a conflict.
- Different collection IDs with identical values are legitimate acquisitions. No content-only deduplication.
- Retry timeouts, lost acknowledgements, 408, 429, and 5xx with capped exponential backoff/jitter and Retry-After when supplied. A lost response may follow a successful receiver commit: retrying must return duplicate.
- Pause publishing on 401/403 and surface credential/authorization failure. Quarantine permanent validation, unsupported-version, oversized-event, and 409 failures; do not endlessly retry. Preserve rejected events and sanitized reasons for correction/review.
- Consumer processing is idempotent. An HTTP acknowledgement guarantees durable receipt only, not completed analysis or notification delivery.
- Preserve ordering per incident and use source timestamps for observations. Late history cannot overwrite newer latest-state data.

No live exporter, receiver, or delivery-idempotency implementation is supplied here. These are acceptance requirements for later implementation.

## 6. Backlog and retention

Outbox defaults: 100,000 pending events or 256 MiB, whichever limit is reached first. At capacity, suspend enrollment, preserve a durable catch-up cursor, and continue local event storage. Raise an export incident. After recovery, enroll retained events in order without new IDs. Pending and not-yet-enrolled history is exempt from retention pruning.

Disk-space exhaustion is a separate storage incident: journal it, use the bounded memory queue, count any overflow, and report gaps after recovery. Do not promise lossless buffering through unlimited outages. Back up SQLite consistently using the established backup process.

## 7. Versioning and migration

1. PRD revision 2.0 describes configuration v2 and the first proposed wire version 1.0.0. These are separate version domains.
2. No prior deployed wire schema was available. Do not silently reinterpret any existing Napoleon contract. Negotiate an explicit mapping when one is discovered.
3. Every payload-shape or semantic change gets a new exact version and schema artifact. Breaking changes increment major; additive changes require a new negotiated minor because v1 rejects extra fields. Never mutate the meaning of published units, metrics, IDs, or timestamps.
4. Source adapters must agree supported fields/versions before publication. If discovery is missing, stale, or incompatible, retain local data and expose integration-pending status.
5. Preserve old sensor IDs when migrating config v1. Map protected hardware identity locally and keep original room history.
6. If the original environment_reading table exists, create three observation events for each valid legacy row (temperature, humidity, RSSI) sharing a collection ID. Preserve observed_at as BLE receipt time and received_at as persisted_at only if validated as such; infer neither source precision nor missing times.
7. Use a migration_map keyed by stable legacy store identity, reading ID, and metric. Reuse IDs on retries; preserve original tables until row counts and exports reconcile and backup restoration is tested.
8. Migration requires an inspected database schema. This repository has no deployed database to migrate. No executable data migration is claimed.

## 8. Validation and acceptance

Run the README commands. The examples are synthetic, including the stale cloud event; they establish no household conditions and no guarantee that VeSync supplies measurement timestamps.

The schema and helper cover event shape, known metric/unit pairs, timestamp consistency, finite numbers, adapter compatibility, optional state, and conflicting duplicates within an export. Tests cover valid examples and malformed cases. The acknowledgement schema and helper check success response shape and request-ID correlation.

Later integration gates remain: actual model/capabilities, account authentication, timestamp meaning, parser accuracy, metric-specific policy, source authorization, clock skew against now, persistence/atomicity, restart recovery, duplicate delivery, notification behavior, and the seven-day hardware trial. Matching an app display establishes extraction consistency, not independent accuracy.

## References

- [pyvesync](https://github.com/webdjoe/pyvesync)
- [Home Assistant VeSync support and cloud polling](https://www.home-assistant.io/integrations/vesync/)
- [Python jsonschema validation and format checking](https://python-jsonschema.readthedocs.io/en/stable/validate/)
