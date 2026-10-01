# Weather values in the Mimir monitor

Show latest valid modeled values, sample means over trailing 1h and 24h, and
24h min/max for five weather metrics, with units and age. Summarize the existing
SQLite snapshot in the pilot report. Use the latest revision for each distinct
sample, current-at-acquisition valid quality only, fixed location epoch, and valid
time window. Missing periods are not filled; show the 24h sample count and explain
partial history. Solar values remain provider interval means, not instantaneous.

Export only numeric allowlisted summary fields through the existing private
Prometheus bridge. Permit negative temperature and dew point summaries without
relaxing counters or timestamps. Preserve pilot deadline and all sensor controls.

Verify exact summary values, revision replacement, stale/suspect exclusion,
negative values, absent summaries and live terminal output; run the full suite,
deploy both sides, verify live data and commit/push.

Verified deployment: all 75 tests pass locally and on Odin. Live Mimir output
shows all five value rows, units, latest-valid age, sample counts, 1h/24h means
and 24h range alongside the existing sensor and pilot displays. The first check
showed 14 distinct valid samples per metric. Temperature was 19.2°C latest,
19.4°C 1h mean, 19.6°C available-history 24h mean, range 19.1–20.3°C.
The update uses existing report/export timers; no pilot restart or deadline
change was required. Restart an already-open terminal monitor to load the new
renderer; subsequent updates occur automatically.
