# Mimir terminal monitor update

Extend `/usr/local/bin/kari-monitor` through an isolated launcher using its existing
installed Kári package. Preserve local sensor queries, CLI options, curses controls,
refresh cadence, and service ownership. Append weather pilot lines to the existing
renderer; do not replace the installed collector package with the different weather
pilot package in this repository.

Read only the allowlisted numeric pilot metrics from Odin's private Prometheus
endpoint, with a two-second timeout and one-minute cache. Display transport failure
explicitly even when cached data is available; mark active reports older than 20
minutes stale. Do not expose coordinates, addresses, configuration, or credentials.
Show phase, assessment, start/end in local time, countdown, report age, backup,
requests, and five-metric coverage. Explain modeled weather and Mimir-only Bluetooth.

Completion: tests for current, stale, absent, failed, and invalid responses;
verify real `--once` output and interactive scroll/quit on Mimir; preserve the old
launcher for rollback; commit and push implementation and deployment evidence.

## Deployment evidence — 2026-10-01

Installed the two isolated Python helpers in `/usr/local/libexec` as root:wheel,
mode 0644; replaced the requested launcher as root:wheel, mode 0755. The previous
launcher is preserved at `/usr/local/bin/kari-monitor.pre-weather-20261001`.
Access used the existing Odin-to-Mimir SSH route. No collector or Bluetooth
services were changed.

The live single-frame output retained all three indoor devices and added the
weather pilot: running/in progress, verified backup, 12/12 successful requests,
all five metrics, no stale/suspect samples, and the correct October 7 deadline.
An actual interactive terminal run rendered both sections; scrolling exposed
all weather rows, refresh worked, and `q` exited cleanly. All 71 repository tests
passed, including current/stale/unavailable/failed-cache and privacy cases.

Rollback: restore the preserved launcher with its existing executable mode.
The helpers can remain unused. Odin outages cannot stop local sensor monitoring;
the weather supplement displays an unreachable warning and cached data if present.
