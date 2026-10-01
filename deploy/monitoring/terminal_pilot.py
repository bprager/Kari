"""Read-only, bounded weather-pilot supplement for Mimir's terminal monitor."""
from datetime import datetime
import json
import math
import time
from urllib.parse import urlencode
from urllib.request import urlopen

METRICS = {'temperature':'Temperature', 'relative_humidity':'Humidity',
           'dew_point':'Dew point', 'wind_speed':'Wind speed', 'shortwave_radiation':'Solar radiation'}
SCALARS = {'phase','assessment','report_available','report_timestamp_seconds',
           'started_timestamp_seconds','ends_timestamp_seconds','backup_verified',
           'polls_successful','polls_attempted'}
SERIES = {'samples','coverage_fraction','stale','suspect'}
ENDPOINT = 'http://192.168.1.3:19090/api/v1/query?'+urlencode(
    {'query':'{__name__=~"kari_weather_pilot_.+",job="odin-textfile"}'})


def parse(response):
    if response['status'] != 'success':
        raise ValueError('Monitoring query failed')
    result = {}
    for row in response['data']['result']:
        name = row['metric']['__name__'].removeprefix('kari_weather_pilot_')
        metric = row['metric'].get('metric','')
        if not ((name in SCALARS and not metric) or (name in SERIES and metric in METRICS)):
            continue
        value = float(row['value'][1])
        if not math.isfinite(value) or value < 0:
            raise ValueError('Invalid monitoring number')
        result[name,metric] = value
    return result


def fetch():
    with urlopen(ENDPOINT, timeout=2) as response:
        body = response.read(1024*1024+1)
    if len(body) > 1024*1024:
        raise ValueError('Monitoring response too large')
    return parse(json.loads(body))


class Reader:
    def __init__(self):
        self.next_read = float('-inf')
        self.values = {}
        self.unreachable = False

    def read(self):
        now = time.monotonic()
        if now >= self.next_read:
            self.next_read = now+60
            try:
                self.values = fetch()
                self.unreachable = False
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                self.unreachable = True
        return self.values, self.unreachable


def render(values, *, now=None, unreachable=False):
    now = time.time() if now is None else now
    def get(name,metric='',default=None): return values.get((name,metric),default)
    lines = [('', 'normal'), (' WEATHER PILOT · fixed HOME · Odin', 'accent')]
    if unreachable:
        lines.append(('   ODIN UNREACHABLE — cached information below, if available.', 'warning'))
    if get('report_available') != 1 or get('phase') is None:
        lines[1] = (' WEATHER PILOT · Odin · UNAVAILABLE', 'warning')
        lines.append(('   Pilot report is unavailable; local sensor monitoring continues.', 'warning'))
    else:
        phase = {0:'AWAITING CONFIGURATION',1:'RUNNING',2:'COMPLETED'}.get(get('phase'),'UNKNOWN')
        assessment = {0:'NOT STARTED',1:'IN PROGRESS',2:'PASSED',3:'NEEDS REVIEW'}.get(get('assessment'),'UNKNOWN')
        age = now-get('report_timestamp_seconds',default=0)
        stale = age > 1200 and get('phase') != 2
        future = age < -60
        status = 'STALE REPORT' if stale else 'REPORT CLOCK INVALID' if future else 'final report' if get('phase') == 2 else 'report current'
        lines.append((f'   {phase} · {assessment} · {status} · age {max(0,age)//60:.0f}m',
                      'warning' if stale or future or unreachable else 'accent'))
        for label, key in [('Started','started_timestamp_seconds'),('Automatic stop','ends_timestamp_seconds')]:
            value = get(key)
            if value is not None:
                try: date = datetime.fromtimestamp(value).astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')
                except (OverflowError,OSError,ValueError): date = 'invalid timestamp'
                lines.append((f'   {label}: {date}', 'normal'))
        end = get('ends_timestamp_seconds')
        if end is not None:
            remaining = max(0,int(end-now))
            lines.append((f'   Remaining: {remaining//86400}d {remaining%86400//3600}h {remaining%3600//60}m', 'normal'))
        backup = 'verified' if get('backup_verified') == 1 else 'not verified'
        success,attempts=get('polls_successful'),get('polls_attempted')
        counts = f'{success:g}/{attempts:g}' if success is not None and attempts is not None else 'unavailable'
        lines.append((f'   Backup: {backup} · Successful requests: {counts}', 'normal'))
        lines.append(('   METRIC               SAMPLES    COVERAGE    STALE  SUSPECT', 'muted'))
        for metric,label in METRICS.items():
            def fmt(key,percent=False):
                value=get(key,metric)
                return '—' if value is None else f'{value:.1%}' if percent else f'{value:g}'
            lines.append((f'   {label:<20}{fmt("samples"):>7}{fmt("coverage_fraction",True):>12}{fmt("stale"):>9}{fmt("suspect"):>9}', 'normal'))
    lines.extend([
        ('   Open-Meteo modeled estimates, not measurements at HOME. CC BY 4.0.', 'muted'),
        ('   15m collection/reports · daily backups · automatic final assessment.', 'muted'),
        ('   Solar coverage looks backward. Publishing remains disabled.', 'muted'),
        ('   Bluetooth/BLE: Mimir only. Weather: Odin. HOME location kept private.', 'accent'),
    ])
    return lines
