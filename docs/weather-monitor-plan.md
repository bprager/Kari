# Weather pilot monitor design and implementation plan

Extend the existing private Kari Operations dashboard on Odin, preserving its
Mimir sensor panels. Read only the coordinate-free pilot report. Export a fixed
allowlist of numeric metrics through a separate node-exporter textfile every
minute, without modifying the existing Mimir metrics bridge or the pilot.

Display pilot state, assessment, exact start/end, remaining time, report age,
backup verification, successful/attempted requests, sample counts, stale/suspect
counts and coverage for all five metrics. Explain modeled weather, fixed HOME,
15-minute cadence, automatic final report, disabled publishing and host placement.
Do not expose the address, coordinates, protected configuration, or raw responses.

A missing/malformed report exports report_available=0 and no invented progress.
Report age is computed against wall time, so a stopped report timer remains visible.
Mark reports older than 20 minutes as stale while the trial is active. Completed
reports remain historical evidence. Only numeric allowlisted fields and fixed
metric names enter Prometheus. All writes use atomic replacement.

Implementation and completion checks:
- [x] Add report exporter and tests for running/completed/waiting, malformed input,
  privacy allowlisting and atomic file replacement.
- [x] Add isolated service/timer and dashboard augmentation script; preserve
  existing panels and make repeated augmentation idempotent.
- [x] Run the full test suite and deploy the exporter as kari, with write access
  only to its monitoring output directory.
- [x] Verify actual node-exporter metrics, Prometheus queries, the loaded Grafana
  dashboard and, where accessible, the rendered view.
- [x] Record evidence, commit and push the monitor branch.
