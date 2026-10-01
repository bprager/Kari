#!/bin/sh
# Run as root after unpacking the verified checkout to /opt/kari-weather-pilot.
# No Bluetooth operations; no changes to /opt/kari or /etc/kari.
set -eu
pilot_root=/opt/kari-weather-pilot
pilot_config=/etc/kari-weather-pilot
pilot_data=/var/lib/kari/weather-pilot
test "$(id -u)" -eq 0
test -f "$pilot_root/deploy/kari-weather-pilot.service"
getent passwd kari >/dev/null
install -d -m 0750 -o root -g kari "$pilot_config"
install -d -m 0700 -o kari -g kari "$pilot_data"
if ! test -f "$pilot_config/config.yaml"; then
    "$pilot_root/.venv/bin/python" - "$pilot_root" "$pilot_config" <<'PY'
from pathlib import Path
import sys,yaml
root,destination=map(Path,sys.argv[1:])
config=yaml.safe_load((root/'examples/config-v2.yaml').read_text())
config['collector']['database_path']='/var/lib/kari/weather-pilot/weather.sqlite3'
config['adapters']['weather'].update(enabled=False,coordinates_file='/etc/kari-weather-pilot/coordinates.local.json',eligible_use_confirmed=True,current_semantics_verified=True)
(destination/'config.yaml').write_text(yaml.safe_dump(config,sort_keys=False))
PY
    chown root:kari "$pilot_config/config.yaml"
    chmod 0640 "$pilot_config/config.yaml"
fi
for unit in kari-weather-pilot.service kari-weather-pilot-report.service kari-weather-pilot-report.timer; do
    install -m 0644 "$pilot_root/deploy/$unit" "/etc/systemd/system/$unit"
done
systemd-analyze verify /etc/systemd/system/kari-weather-pilot.service /etc/systemd/system/kari-weather-pilot-report.service /etc/systemd/system/kari-weather-pilot-report.timer
systemctl daemon-reload
systemctl enable --now kari-weather-pilot.service kari-weather-pilot-report.timer
for attempt in 1 2 3 4 5 6 7 8 9 10; do
    if test -f "$pilot_data/pilot.json"; then break; fi
    sleep 1
done
test -f "$pilot_data/pilot.json"
systemctl start kari-weather-pilot-report.service
