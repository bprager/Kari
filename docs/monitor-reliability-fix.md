# Weather monitor reliability repair — 2026-10-02

## Observed cause

Mimir reproduced `ODIN UNREACHABLE`. Directly calling its fetch function raised
connection refused for Odin port 19090. Odin was reachable over SSH, the weather
collector was active, and its coordinate-free metrics export was current.
`prager-odin-prometheus` was stopped with exit code 0 and finish time
2026-10-03 00:23:09 UTC. The reason it was stopped was not established.
Starting that existing container restored its endpoint. No collector restart,
Bluetooth change, or pilot deadline change was needed.

## Repair

The terminal monitor no longer infers host health from a monitoring request.
It reports weather-data refresh failures, identifies its selected data source,
and distinguishes HTTP failures from malformed/missing data and timeouts.
If Prometheus fails, is missing weather data, or has an old active report, the
monitor reads the existing public-to-local-users numeric export over its existing
SSH connection. Strict host-key checking stays enabled; no new credentials or
remote commands beyond reading that fixed file are introduced.

Both responses are bounded and filtered to known numeric metrics. If both fail,
previous values remain visibly marked cached; their age continues increasing.
Retries are automatic. Interactive reads run in a single background thread so
network delays do not block scrolling/quit or indoor readings. Single-frame mode
waits for bounded reads to produce useful output. No new background service is
installed. An already-open monitor must be reopened to load the fix.

## Verification

All 82 tests pass, including missing/malformed/stale primary data, fallback,
cache preservation, recovery, and nonblocking single-inflight behavior. Live tests
on Mimir forced the primary request to fail while using real SSH data, yielding
64 current metrics; then forced both paths to fail and verified marked cached
values; finally restored real requests and verified recovery to Prometheus.
Live `kari-monitor --once` showed all indoor sensors and five weather readings,
184/184 successful pilot requests, and a verified backup. The separate interactive
SSH invocation encountered a host-key mismatch; no host keys were changed or
verification disabled. Background behavior was verified by controlled tests.
