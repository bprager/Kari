"""Unattended, fixed-duration weather pilot. No Bluetooth or publishing."""
import argparse
from contextlib import contextmanager, closing
from datetime import datetime, timezone, timedelta
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import sqlite3
import tempfile
import threading
import time

from kari.config import load_config, load_coordinates
from kari.reading_summary import summarize
from kari.store import Store, canonical, seconds
from kari.weather import VARIABLES, stamp, utcnow
from kari.worker import Worker
from scripts.validate_contract import validate_event, loads

DURATION = 7 * 86400
ATTRIBUTION = 'Weather data by Open-Meteo (https://open-meteo.com/), CC BY 4.0. Processed by Kari; modeled estimates, not measurements at HOME.'


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def exclusive(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def contents(connection):
    if connection.execute('PRAGMA user_version').fetchone()[0] != 1:
        raise ValueError('Unexpected backup schema')
    if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise ValueError('Backup integrity failed')
    digest = hashlib.sha256()
    count = 0
    for row in connection.execute('SELECT payload FROM events ORDER BY seq'):
        event = loads(row[0]); validate_event(event)
        digest.update((canonical(event) + '\n').encode()); count += 1
    return dict(events=count, export_sha256=digest.hexdigest(),
                polls=connection.execute('SELECT COUNT(*) FROM polls').fetchone()[0])


def check_backup(path, expected):
    # Verification must never initialize, migrate, or otherwise change a backup.
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as restored:
        if contents(restored) != expected:
            raise ValueError('Backup contents differ from source manifest')


def verified_backup(snapshot, backup):
    manifest = Path(str(backup) + '.manifest.json')
    existed = backup.exists()
    valid = False
    if backup.exists() and manifest.exists():
        try:
            expected = loads(manifest.read_text())
            check_backup(backup, expected)
            valid = True
        except (ValueError, OSError, sqlite3.Error):
            pass
    if not valid:
        expected = contents(snapshot.connection)
        with tempfile.TemporaryDirectory(prefix='.backup-', dir=backup.parent) as folder:
            candidate = Path(folder) / 'backup.sqlite3'
            snapshot.backup(candidate)
            check_backup(candidate, expected)
            os.replace(candidate, backup)
            atomic_write(manifest, canonical(expected) + '\n')
    return dict(file=backup.name, integrity='ok', export_verified=True,
                repaired=existed and not valid, **expected)


def initial_state():
    return dict(version=1, phase='awaiting_configuration', started_at=None, ends_at=None,
                reason='fixed_home_configuration_required', duration_seconds=DURATION)


class Pilot:
    def __init__(self, config, state_path, reports):
        self.config_path, self.state_path, self.reports = Path(config), Path(state_path), Path(reports)
        self.state = loads(self.state_path.read_text()) if self.state_path.exists() else initial_state()
        self.worker = None
        self.store = None
        self.next_config_check = 0
        self.last_saved = None

    def save(self):
        body = canonical(self.state) + '\n'
        if body != self.last_saved:
            atomic_write(self.state_path, body)
            self.last_saved = body

    def begin(self, at):
        if self.state['started_at'] is None:
            self.state.update(phase='running', started_at=at,
                              ends_at=stamp(datetime.fromtimestamp(seconds(at) + DURATION, timezone.utc)),
                              reason=None)
            self.save()

    def close(self):
        if self.store is not None:
            self.store.close()
            self.store = None
        self.worker = None

    def step(self, mono, at=None):
        at = at or utcnow()
        if self.state['ends_at'] is not None and seconds(at) >= seconds(self.state['ends_at']):
            self.state.update(phase='completed', reason=None)
            self.save(); self.close()
            return 'completed'
        if self.worker is None:
            if mono < self.next_config_check:
                return self.state['phase']
            self.next_config_check = mono + 60
            try:
                cfg = load_config(self.config_path)
                if not cfg.weather.enabled:
                    raise ValueError('Configuration not activated')
                load_coordinates(cfg.weather)
                self.store = Store(cfg.database_path, cfg.collector_id)
                self.store.mark_interrupted(cfg.weather.device_id)
                self.worker = Worker(self.store, cfg.weather, buffer_groups=cfg.buffer_groups)
                self.state['reason'] = None
                self.state['phase'] = 'running' if self.state['started_at'] else 'verifying_provider'
            except (ValueError, OSError, sqlite3.Error):
                self.close()
                self.state.update(phase='awaiting_configuration' if not self.state['started_at'] else 'configuration_unavailable',
                                  reason='fixed_home_configuration_required')
                self.save()
                return self.state['phase']
        # A request must fit before the deadline. Do not count late writes as pilot evidence.
        if self.state['ends_at'] and seconds(self.state['ends_at']) - seconds(at) <= self.worker.cfg.request_timeout_seconds:
            return 'finishing'
        outcome = self.worker.tick(mono)
        if self.state['started_at'] is None and outcome == 'success':
            poll = self.store.connection.execute('SELECT * FROM polls ORDER BY attempted_at DESC LIMIT 1').fetchone()
            latest = self.store.latest()
            if (poll and poll['outcome'] == 'success' and loads(poll['missing']) == []
                and len(latest) == 5 and all(e['data']['quality'] == 'valid' for e in latest)
                and poll['latest_valid_at'] is not None
                and 0 <= poll['received_at'] - poll['latest_valid_at'] <= self.worker.cfg.max_age_seconds):
                self.begin(stamp(datetime.fromtimestamp(poll['received_at'], timezone.utc)))
                self.state['provider_verification'] = dict(metrics=5, units_verified=True,
                    utc_verified=True, solar_interval_seconds=next(e['data']['temporal_support']['nominal_step_seconds'] for e in latest if e['data']['metric']=='shortwave_radiation'))
        self.save()
        return outcome

    def report(self, at=None):
        at = at or utcnow()
        state = loads(self.state_path.read_text()) if self.state_path.exists() else self.state
        report = dict(state, reported_at=at, attribution=ATTRIBUTION, publishing_enabled=False)
        if not state['started_at']:
            report.update(acceptance='not_started', remaining_seconds=None)
            self._write_reports(report)
            return report
        start = seconds(state['started_at'])
        end = min(seconds(at), seconds(state['ends_at']))
        finished = seconds(at) >= seconds(state['ends_at'])
        report['phase'] = 'completed' if finished else state['phase']
        report['remaining_seconds'] = max(0, seconds(state['ends_at']) - seconds(at))
        cfg = load_config(self.config_path)
        self.reports.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Consistent snapshot avoids racing collection while checking export and aggregates.
        with Store(cfg.database_path, cfg.collector_id) as source:
            temp_dir = tempfile.TemporaryDirectory(prefix='.snapshot-', dir=self.reports)
            try:
                snapshot_path = Path(temp_dir.name) / 'snapshot.sqlite3'
                source.backup(snapshot_path)
                with Store(snapshot_path, cfg.collector_id) as snapshot:
                    report.update(self._summarize(snapshot, start, end, cfg))
                    day = min(7, max(0, int((end - start) // 86400)))
                    backup = self.reports / ('final-backup.sqlite3' if finished else f'day-{day:02d}-backup.sqlite3')
                    report['backup'] = verified_backup(snapshot, backup)
            finally:
                temp_dir.cleanup()
        report['database_bytes'] = Path(cfg.database_path).stat().st_size
        report['wal_bytes'] = Path(str(cfg.database_path)+'-wal').stat().st_size if Path(str(cfg.database_path)+'-wal').exists() else 0
        expected = max(1, math.ceil((end - start) / cfg.weather.poll_interval_seconds))
        report['expected_cadence_slots'] = expected
        minimum = min((v['coverage_fraction'] for v in report['metrics'].values()), default=0)
        report['acceptance'] = ('passed' if minimum >= .95 and report['backup']['export_verified'] and report['polls']['successful'] >= .95 * expected else 'needs_review') if finished else 'in_progress'
        self._write_reports(report)
        return report

    def _summarize(self, store, start, end, cfg):
        c = store.connection
        # The response that starts the pilot was requested before its start time.
        # Include requests overlapping the window, including unfinished attempts.
        polls = dict(c.execute('''SELECT COALESCE(SUM(scheduled_count),0) scheduled,
            COALESCE(SUM(attempted),0) attempted, COALESCE(SUM(outcome='success'),0) successful,
            COALESCE(SUM(transport_success),0) transport_successful,
            COALESCE(SUM(gaps),0) gaps FROM polls WHERE COALESCE(received_at,attempted_at)>=? AND attempted_at<=?''', (start,end)).fetchone())
        polls['outcomes'] = {r[0]:r[1] for r in c.execute('SELECT outcome,COUNT(*) FROM polls WHERE COALESCE(received_at,attempted_at)>=? AND attempted_at<=? GROUP BY outcome',(start,end))}
        polls['missing_metrics'] = sum(len(loads(r[0])) for r in c.execute('SELECT missing FROM polls WHERE COALESCE(received_at,attempted_at)>=? AND attempted_at<=?',(start,end)))
        metrics = {m[1]:dict(samples=0,stale=0,suspect=0,revisions=0,coverage_fraction=0) for m in VARIABLES}
        total = 0
        export_path = self.reports / 'events.jsonl'
        fd, tmp = tempfile.mkstemp(prefix='.events-', dir=self.reports)
        try:
            with os.fdopen(fd,'w') as stream:
                rows = c.execute("SELECT payload FROM events WHERE event_type='weather_context' AND collected_at>=? AND collected_at<=? ORDER BY seq",(start,end))
                for row in rows:
                    event = loads(row[0]); validate_event(event)
                    stream.write(canonical(event)+'\n'); total += 1
                    data = event['data']
                    metrics[data['metric']]['revisions'] += int(data['revision'] > 1)
                stream.flush(); os.fsync(stream.fileno())
            os.replace(tmp,export_path)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)
        intervals = {metric:[] for metric in metrics}
        for event in store.iter_query(as_of=stamp(datetime.fromtimestamp(end,timezone.utc))):
            if not start <= seconds(event['collected_at']) <= end:
                continue
            data = event['data']; metric = data['metric']; stats = metrics[metric]
            stats['samples'] += 1
            stats['stale'] += int(data['valid_time_freshness']=='stale')
            stats['suspect'] += int(data['quality']=='suspect')
            if data['quality'] != 'valid' or data['valid_time_freshness'] != 'current': continue
            valid = seconds(data['valid_at']); support = data['temporal_support']
            if support['statistic']=='mean':
                left,right = seconds(support['interval_start']), valid
            else:
                # Coverage is cadence support, never a claim of physical observation.
                left,right = valid,valid+support['nominal_step_seconds']
            left,right = max(start,left),min(end,right)
            if right>left: intervals[metric].append((left,right))
        for metric, spans in intervals.items():
            covered=0; previous_end=start
            for left,right in sorted(spans):
                covered += max(0,right-max(left,previous_end))
                previous_end=max(previous_end,right)
            metrics[metric]['coverage_fraction']=min(1,covered/max(1,end-start))
        incidents = [loads(r[0])['data'] for r in c.execute("SELECT payload FROM events WHERE event_type='health_event' AND collected_at>=? AND collected_at<=? ORDER BY seq",(start,end))]
        return dict(polls=polls,metrics=metrics,events=total,incidents=incidents,
                    readings=summarize(store, end, cfg.weather.location_epoch_id))

    def _write_reports(self, report):
        atomic_write(self.reports/'latest.json',canonical(report)+'\n')
        text = '# Kári seven-day weather pilot\n\n'
        text += f"State: {report['phase']}\n\nStarted: {report['started_at'] or 'not started'}\n\nEnds: {report['ends_at'] or 'not scheduled until valid HOME configuration'}\n\n"
        text += f"Assessment: {report['acceptance']}\n\n"
        if 'metrics' in report:
            text += '| Metric | Samples | Coverage | Stale | Suspect | Corrections |\n|---|---:|---:|---:|---:|---:|\n'
            for name,v in report['metrics'].items():
                text += f"| {name} | {v['samples']} | {v['coverage_fraction']:.1%} | {v['stale']} | {v['suspect']} | {v['revisions']} |\n"
            text += '\nPolls: '+canonical(report['polls'])+'\n\nBackup: '+canonical(report['backup'])+'\n\n'
        text += ATTRIBUTION+'\n'
        atomic_write(self.reports/'latest.md',text)
        if report['phase']=='completed':
            atomic_write(self.reports/'final.json',canonical(report)+'\n')
            atomic_write(self.reports/'final.md',text)


def main(argv=None):
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['run','report','status'])
    p.add_argument('--config',required=True,type=Path)
    p.add_argument('--state',required=True,type=Path)
    p.add_argument('--reports',required=True,type=Path)
    args=p.parse_args(argv)
    pilot=Pilot(args.config,args.state,args.reports)
    try:
        if args.command=='report':
            with exclusive(str(args.state)+'.report.lock'):
                report=pilot.report()
                print(canonical({k:report.get(k) for k in ('phase','started_at','ends_at','acceptance')}))
        elif args.command=='status':
            print(canonical(pilot.state))
        else:
            with exclusive(str(args.state)+'.controller.lock'):
                stop=threading.Event()
                for sig in (signal.SIGINT,signal.SIGTERM): signal.signal(sig,lambda *_:stop.set())
                while not stop.is_set():
                    if pilot.step(time.monotonic())=='completed':
                        break
                    stop.wait(1)
    except (ValueError,OSError,sqlite3.Error):
        p.exit(1,'Pilot operation failed; protected configuration or storage needs attention.\n')
    finally:
        pilot.close()


if __name__=='__main__':
    main()
