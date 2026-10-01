# Private weather pilot monitoring

`kari.monitor` reads the protected pilot report and atomically exports only
allowlisted numeric statistics. It never reads the HOME configuration or database.
The dedicated timer runs every minute at second 45; pilot reports update every
15 minutes at second 30. The existing Mimir metrics bridge is independent.

Deployment on Odin:

1. Install `kari/monitor.py` in the pilot package at `/opt/kari-weather-pilot`.
2. Install the two unit files in `/etc/systemd/system`. The existing
   `hermodr-metrics` group grants access to the existing textfile directory;
   report access stays with the `kari` account.
3. Back up the existing provisioned `kari.json`. Run `update_dashboard.py`
   with that file as input and a temporary output, then replace the original.
   The updater preserves unrelated panels and replaces only panels it owns.
4. Run `systemctl daemon-reload`, `systemctl enable --now kari-weather-monitor.timer`,
   then `systemctl start kari-weather-monitor.service`.
5. Confirm `kari-weather-pilot.prom` exists, query `kari_weather_pilot_phase` in
   Prometheus, and inspect the existing `kari-operations` dashboard in Grafana.
   Grafana picks up provisioning changes within 30 seconds; reload the page.

No Grafana alerts, email, publishing, Bluetooth, or collection settings change.
The reporting-health panel becomes unhealthy for an unreadable report or an
active-trial report more than 20 minutes old. Completed reports are immutable
historical evidence and do not expire. A missing report does not mean success;
it removes old pilot values and exports availability zero. Prometheus scrape
availability remains covered by the existing infrastructure monitoring.

To roll back, stop/disable the monitor timer, remove its single `.prom` file,
and restore the saved dashboard. The weather collection and reports continue.

## Verified deployment — 2026-10-01 UTC

All 12 original panels remain unchanged. Grafana loaded 16 additional panels;
every data query returned live data. Browser inspection confirmed running and
in-progress status, the September 30–October 7 21:16 Pacific window, report
freshness, verified backup, ten successful requests, all five metrics, and no
stale/suspect samples. Early coverage was approximately 99.7% for instantaneous
metrics and 89.6% for backward-looking solar intervals; these values will change.

The private dashboard is `http://192.168.1.3:3000/d/kari-operations` and requires
Grafana sign-in. Monitoring output contains no HOME address or coordinates.
