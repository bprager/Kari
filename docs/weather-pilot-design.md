# Unattended seven-day weather pilot

The operator approved location sharing and autonomous pilot setup on 2026-09-30.
This approval covers Open-Meteo fixed HOME collection for personal use. It does
not enable receiver publishing or change the fixed-location rule. Bluetooth
services belong on Mimir only; the weather pilot uses no Bluetooth.

## Operating design

Deploy this checkout separately at `/opt/kari-weather-pilot` on Odin, under the
existing unprivileged `kari` account. Use a dedicated database and protected
configuration under `/var/lib/kari/weather-pilot` and `/etc/kari-weather-pilot`.
Preserve the older Kári runtime, its database, and its metrics export timer.

One systemd-supervised process waits for valid fixed-location configuration,
performs the normal weather polling/backoff loop, and freezes a seven-day deadline
when the first response yields all five valid, current metrics. It never invents
coordinates, uses a phone's latest position, or geocodes an address. No source
available at setup may be treated as HOME unless it actually identifies HOME.
A missing location leaves the service in `awaiting_configuration`, explicitly
not a started seven-day data trial. Startup and reboot do not reset the deadline.

A separate persistent systemd timer runs every 15 minutes. It writes an atomic
protected status report, validates every exported event, and creates one
consistent SQLite backup per day. Each new backup is reopened independently,
checked for integrity, and compared with a digest of its own exported immutable
history. The final report is produced automatically after the frozen deadline.
Collection then stops; retained reports/history remain accessible. Reports include
actual start/end, remaining time, poll counts, represented-time coverage by
metric, invalid/missing/stale values, revisions, incidents, and storage growth.
No email, SMS, or publishing is enabled.

Outage/restart behavior is exercised through the existing fault-injection and
process-crash tests before deployment. A safe independent acceptance drill uses
a separate temporary database, no Bluetooth, and no network/firewall changes;
it cannot interrupt unrelated services. Production outages, if any, are recorded
honestly. The trial never fabricates outages or missed history.

## Acceptance and completion

- [x] Add tests for missing configuration, restart-preserved deadline, exact stop,
  report generation, coverage, and backup restoration.
- [x] Implement `kari.pilot` controller/report commands and protected systemd units.
- [x] Verify all existing tests plus pilot tests and compile/format checks.
- [x] Deploy independently on Odin; verify file ownership, service and timers,
  and preserve preexisting service states.
- [ ] Start actual data collection only with identified fixed HOME coordinates;
  inspect first real response semantics and all five metrics before trial starts.
- [ ] Record observed deployment state and exact automatic schedule. Commit and
  push code, tests, design, preference mirror, and deployment evidence.

Personal home automation is eligible for Open-Meteo's free noncommercial tier
under the [terms reviewed at setup](https://open-meteo.com/en/terms). The
[provider documentation](https://open-meteo.com/en/docs) defines current model
values and backward-looking response intervals. Carry its attribution alongside
reports. Raw bodies, coordinates, and request URLs stay out of reports and logs.


## Deployment evidence — 2026-10-01 UTC

- The pilot controller is installed, enabled, and running on Odin as `kari`.
- The persistent report timer is enabled for minutes 00, 15, 30, and 45 at
  second 30 UTC; it also runs one minute after boot.
- Reports are at `/var/lib/kari/weather-pilot/reports/latest.json` and
  `latest.md`; a completed trial writes `final.json` and `final.md`.
- The controller checks configuration every 60 seconds while awaiting setup.
- 61 tests passed on Odin (Python 3.14.4); the same suite and pilot regressions
  run locally. Review reproduced and fixed incomplete-backup verification and
  stale-evidence coverage errors. Backup checks now compare a restored database
  with a saved source digest/count, and stale samples do not count toward
  acceptance coverage.
- Preexisting `kari-collector.service` and `kari-ble-recovery.timer` remain
  disabled on Odin. `kari-metrics-export.timer` remains enabled. No Bluetooth
  or OpenClaw services were started or reconfigured.
- Location sharing was approved, but no usable fixed HOME coordinates were
  found in existing configuration or the checked location registry. The latter
  contained only a proposed synthetic HOME entry without coordinates.
- **Actual state: awaiting_configuration; the seven-day collection window has
  not started.** The sole missing operator input is the fixed HOME latitude and
  longitude (or an existing protected file that contains them). A request for
  that input is pending. No location was invented or sent to a provider.

Once the missing coordinates are supplied, store them only in
`/etc/kari-weather-pilot/coordinates.local.json` owned by `kari`, mode 0600,
with the matching approval fields and epoch. Set weather enabled in
`/etc/kari-weather-pilot/config.yaml`. The installed controller automatically
validates the configuration and first complete current response, freezes its
seven-day deadline, and runs without further human interaction. Receiver
publishing remains unavailable.
