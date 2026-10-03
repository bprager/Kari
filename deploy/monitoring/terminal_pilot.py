"""Read-only, bounded weather-pilot supplement for Mimir's terminal monitor."""
from datetime import datetime
import json
import math
import time
import re
import subprocess
import threading
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

METRICS = {'temperature':'Temperature', 'relative_humidity':'Humidity',
           'dew_point':'Dew point', 'wind_speed':'Wind speed', 'shortwave_radiation':'Solar radiation'}
SCALARS = {'phase','assessment','report_available','report_timestamp_seconds',
           'started_timestamp_seconds','ends_timestamp_seconds','backup_verified',
           'polls_successful','polls_attempted'}
VALUE_FIELDS = {'value_'+key for key in ('latest','average_1h','average_24h','minimum_24h','maximum_24h')}
SERIES = {'samples','coverage_fraction','stale','suspect','value_latest_valid_timestamp_seconds','value_count_24h'} | VALUE_FIELDS
UNITS = {'temperature':'°C','relative_humidity':'%','dew_point':'°C','wind_speed':'m/s','shortwave_radiation':'W/m²'}
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
        if not math.isfinite(value) or (value < 0 and not (name in VALUE_FIELDS and metric in ('temperature','dew_point'))):
            raise ValueError('Invalid monitoring number')
        result[name,metric] = value
    return result


def fetch():
    with urlopen(ENDPOINT, timeout=2) as response:
        body = response.read(1024*1024+1)
    if len(body) > 1024*1024:
        raise ValueError('Monitoring response too large')
    return parse(json.loads(body))


def parse_text(body):
    rows = []
    for line in body.splitlines():
        if not line.startswith('kari_weather_pilot_'):
            continue
        match = re.fullmatch(r'(kari_weather_pilot_[a-z0-9_]+)(?:\{metric="([a-z0-9_]+)"\})?\s+(\S+)', line)
        if not match:
            raise ValueError('Invalid weather export')
        name, metric, value = match.groups()
        labels = {'__name__':name}
        if metric: labels['metric'] = metric
        rows.append({'metric':labels, 'value':[0,value]})
    return parse({'status':'success','data':{'result':rows}})


def fetch_direct():
    # Read only the existing coordinate-free export with existing SSH credentials.
    result = subprocess.run(['ssh','-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
        '-o','ConnectTimeout=2','-o','ConnectionAttempts=1','odin',
        'head -c 65537 /home/bernd/Projects/prager.ws/infra/odin/observability/textfile/kari-weather-pilot.prom'],
        capture_output=True, timeout=4, check=True)
    if len(result.stdout) > 65536:
        raise ValueError('Weather export too large')
    return parse_text(result.stdout.decode('utf-8'))


ERRORS = (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError, subprocess.SubprocessError)


def check_values(values):
    if ('report_available','') not in values:
        raise ValueError('Weather metrics missing')
    if values[('report_available','')] == 1 and not all((key,'') in values for key in ('phase','report_timestamp_seconds')):
        raise ValueError('Weather report incomplete')
    return values


def error_kind(error):
    if isinstance(error, HTTPError): return f'HTTP {error.code}'
    if isinstance(error, (ValueError, KeyError, TypeError, AttributeError, IndexError)): return 'invalid or missing data'
    if isinstance(error, (TimeoutError, subprocess.TimeoutExpired)): return 'request timed out'
    return 'connection failed'


class Reader:
    def __init__(self):
        self.next_read = float('-inf')
        self.values = {}
        self.unreachable = False
        self.source = 'loading'
        self.detail = ''
        self.loading = False

    def _refresh(self):
        try:
            try:
                values = check_values(fetch())
                # Bypass stale scrapes when the direct export is newer.
                age = time.time()-values.get(('report_timestamp_seconds',''),0)
                if values.get(('report_available','')) != 1 or (age > 1200 and values.get(('phase','')) != 2):
                    raise ValueError('Weather report stale or unavailable')
                source, detail = 'Prometheus', ''
            except ERRORS as primary:
                values = check_values(fetch_direct())
                source, detail = 'direct export', 'Monitoring endpoint: '+error_kind(primary)
            self.values, self.source, self.detail = values, source, detail
            self.unreachable = False
        except ERRORS as error:
            self.unreachable = True
            self.detail = 'Weather data refresh failed: '+error_kind(error)
        finally:
            self.loading = False

    def read(self, *, background=False):
        now = time.monotonic()
        if now >= self.next_read and not self.loading:
            self.next_read = now+ (15 if self.unreachable else 60)
            self.loading = True
            if background:
                threading.Thread(target=self._refresh,daemon=True).start()
            else:
                self._refresh()
            if self.unreachable: self.next_read = now+15
        return self.values, self.unreachable


def render(values, *, now=None, unreachable=False, source=None, detail=""):
    now = time.time() if now is None else now
    def get(name,metric='',default=None): return values.get((name,metric),default)
    lines = [('', 'normal'), (' WEATHER PILOT · fixed HOME · Odin', 'accent')]
    if unreachable:
        lines.append(('   WEATHER DATA REFRESH FAILED — cached information below, if available.', 'warning'))
    if source:
        lines.append((f'   Data source: {source}', 'muted'))
    if detail:
        lines.append(('   '+detail, 'warning'))
    if get('report_available') != 1 or get('phase') is None:
        lines[1] = (' WEATHER PILOT · Odin · UNAVAILABLE', 'warning')
        lines.append(('   Pilot report is unavailable; local sensor monitoring continues.', 'warning'))
    else:
        phase = {0:'AWAITING CONFIGURATION',1:'RUNNING',2:'COMPLETED'}.get(get('phase'),'UNKNOWN')
        assessment = {0:'NOT STARTED',1:'IN PROGRESS',2:'PASSED',3:'NEEDS REVIEW'}.get(get('assessment'),'UNKNOWN')
        age = now-get('report_timestamp_seconds',default=0)
        stale = age > 1200 and get('phase') != 2
        future = age < -60
        status = 'STALE REPORT' if stale else 'REPORT CLOCK INVALID' if future else 'final report' if get('phase') == 2 else 'cached report' if unreachable else 'report current'
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
        lines.append(('   WEATHER VALUES · latest available modeled readings', 'accent'))
        lines.append(('   METRIC (UNIT)          LATEST   AVG 1h  AVG 24h        RANGE 24h', 'muted'))
        for metric,label in METRICS.items():
            def value(key):
                number = get('value_'+key,metric)
                return '—' if number is None else f'{number:.1f}'
            span = value('minimum_24h')+'–'+value('maximum_24h')
            name = label+' ('+UNITS[metric]+')'
            lines.append((f'   {name:<22}{value("latest"):>7}{value("average_1h"):>9}{value("average_24h"):>9}{span:>17}', 'normal'))
            valid_at = get('value_latest_valid_timestamp_seconds',metric)
            if valid_at is not None:
                reading_age = max(0, int(now-valid_at)//60)
                count = get('value_count_24h',metric,default=0)
                old = ' · OLD READING' if reading_age > 60 else ''
                lines.append((f'     Valid {reading_age}m ago · {count:g} samples in 24h window{old}', 'warning' if old else 'muted'))
        lines.append(('   Sample averages; available history only, no gap filling.', 'muted'))
        lines.append(('   Solar radiation is a provider interval average (W/m²).', 'muted'))
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
