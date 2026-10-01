"""Export only allowlisted pilot report statistics for the private monitor."""
import argparse
from datetime import datetime
import json
import math
import os
from pathlib import Path
import tempfile

METRICS = ('temperature', 'relative_humidity', 'dew_point', 'wind_speed', 'shortwave_radiation')
PHASES = {'awaiting_configuration': 0, 'running': 1, 'completed': 2}
ASSESSMENTS = {'not_started': 0, 'in_progress': 1, 'passed': 2, 'needs_review': 3}


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError('Invalid monitoring value')
    return format(value, '.15g')


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Timestamp requires timezone')
    return parsed.timestamp()


def render(report):
    lines = ['kari_weather_pilot_report_available 1']
    def emit(name, value, labels=''):
        lines.append(f'kari_weather_pilot_{name}{labels} {number(value)}')
    emit('phase', PHASES[report['phase']])
    emit('assessment', ASSESSMENTS[report['acceptance']])
    emit('report_timestamp_seconds', timestamp(report['reported_at']))
    if report['phase'] != 'awaiting_configuration':
        emit('started_timestamp_seconds', timestamp(report['started_at']))
        emit('ends_timestamp_seconds', timestamp(report['ends_at']))
        verified = report['backup']['export_verified']
        if not isinstance(verified, bool):
            raise ValueError('Invalid backup status')
        emit('backup_verified', int(verified))
        for key in ('attempted', 'successful'):
            emit('polls_' + key, report['polls'][key])
        for metric in METRICS:
            if metric not in report['metrics']:
                continue
            stats = report['metrics'][metric]
            for key in ('samples', 'stale', 'suspect', 'coverage_fraction'):
                emit(key, stats[key], '{metric="' + metric + '"}')
    return '\n'.join(lines) + '\n'


def export(source, target):
    try:
        output = render(json.loads(source.read_text()))
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
        output = 'kari_weather_pilot_report_available 0\n'
    fd, temporary = tempfile.mkstemp(prefix='.weather-monitor-', dir=target.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(output)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o644)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    export(args.report, args.output)


if __name__ == '__main__':
    main()
