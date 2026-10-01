"""Local weather operations. Live publishing is deliberately unavailable."""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import signal
import sqlite3
import threading
import time
from uuid import uuid4

from jsonschema import ValidationError
from kari.config import load_config, load_coordinates
from kari.store import Store, canonical, seconds
from kari.weather import normalize, utcnow
from kari.worker import Worker
from scripts.validate_contract import loads


def projection(event, at):
    data = event['data']
    age = seconds(at) - seconds(data['valid_at'])
    return dict(event_id=event['event_id'], collection_id=event['collection_id'],
                device_id=event['device_id'], location_id=event['location_id'],
                collected_at=event['collected_at'], **data,
                age_seconds=age, freshness_now='unknown' if age < 0 else 'current' if age <= event['max_age_seconds'] else 'stale')


@contextmanager
def worker_lock(path):
    lock_path = Path(str(path) + '.weather.lock')
    with lock_path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Weather worker already running') from None
        yield


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', required=True, type=Path)
    commands = p.add_subparsers(dest='command', required=True)
    commands.add_parser('validate-config')
    run = commands.add_parser('run-weather')
    run.add_argument('--once', action='store_true')
    commands.add_parser('resume-weather', help='Clear suspension after correcting local configuration')
    imp = commands.add_parser('import-weather', help='Offline synthetic/redacted current-response import')
    imp.add_argument('--input', required=True, type=Path)
    imp.add_argument('--collected-at', required=True)
    imp.add_argument('--collection-id', default=None)
    for command in ('query-weather', 'export-weather'):
        query = commands.add_parser(command)
        query.add_argument('--start', help='Inclusive UTC valid time')
        query.add_argument('--end', help='Exclusive UTC valid time')
        query.add_argument('--as-of', help='Inclusive collection time cutoff')
        query.add_argument('--metric')
        query.add_argument('--epoch')
        if command == 'query-weather':
            query.add_argument('--at', default=None)
        else:
            query.add_argument('--schema-version', required=True, choices=['1.0.0', '1.1.0'])
    commands.add_parser('health')
    backup = commands.add_parser('backup')
    backup.add_argument('--destination', required=True, type=Path)
    prune = commands.add_parser('prune')
    prune.add_argument('--at', default=None)
    return p


def run_weather(store, cfg, once):
    if not cfg.weather.enabled:
        print('Weather disabled; no provider requests.')
        return
    with worker_lock(store.path):
        store.mark_interrupted(cfg.weather.device_id)
        worker = Worker(store, cfg.weather, buffer_groups=cfg.buffer_groups)
        if once:
            print(canonical({'outcome': worker.tick(time.monotonic())}))
            return
        stopping = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stopping.set())
        while not stopping.is_set():
            worker.tick(time.monotonic())
            stopping.wait(1)


def main(argv=None):
    os.umask(0o077)
    p = parser()
    args = p.parse_args(argv)
    try:
        cfg = load_config(args.config)
        if args.command == 'validate-config':
            if cfg.weather.enabled:
                load_coordinates(cfg.weather)
            print('Configuration valid; weather ' + ('enabled' if cfg.weather.enabled else 'disabled') + '; publishing disabled.')
            return
        with Store(cfg.database_path, cfg.collector_id) as store:
            if args.command == 'run-weather':
                run_weather(store, cfg, args.once)
            elif args.command == 'resume-weather':
                with worker_lock(store.path):
                    if cfg.weather.enabled:
                        load_coordinates(cfg.weather)
                    key = 'worker:' + (cfg.weather.device_id or 'disabled') + ':' + (cfg.weather.location_epoch_id or 'disabled')
                    state = store.get_state(key, {})
                    state.update(suspended=False, failures=0)
                    store.set_state(key, state)
                print('Weather suspension cleared; collection was not started.')
            elif args.command == 'import-weather':
                if not all((cfg.weather.device_id, cfg.weather.location_id, cfg.weather.location_epoch_id)):
                    raise ValueError('Offline import requires opaque weather IDs')
                if args.input.stat().st_size > 65536:
                    raise ValueError('Oversized response')
                body = loads(args.input.read_text())
                result = normalize(body, cfg.weather, args.collected_at)
                events = store.ingest(cfg.weather, result, args.collected_at, args.collection_id or str(uuid4()))
                print(canonical(dict(events_created=len(events), missing_metrics=result.missing, quality_issues=result.issues)))
            elif args.command in ('query-weather', 'export-weather'):
                filters = {k: getattr(args, k) for k in ('start', 'end', 'as_of', 'metric', 'epoch')}
                if args.start and args.end and seconds(args.start) >= seconds(args.end):
                    raise ValueError('Invalid date range')
                if args.command == 'export-weather':
                    for line in store.export([args.schema_version], **filters):
                        print(line)
                else:
                    at = args.at or utcnow()
                    print('[', end='')
                    separator = ''
                    for event in store.iter_query(**filters):
                        print(separator + canonical(projection(event, at)), end='')
                        separator = ','
                    print(']')
            elif args.command == 'health':
                report = store.poll_summary()
                report['weather_enabled'] = cfg.weather.enabled
                report['publishing_enabled'] = False
                now = utcnow()
                report['latest_weather'] = [projection(e, now) for e in store.latest()]
                report['worker'] = store.get_state('worker:' + (cfg.weather.device_id or 'disabled') + ':' + (cfg.weather.location_epoch_id or 'disabled'), {})
                print(canonical(report))
            elif args.command == 'backup':
                store.backup(args.destination)
                print('Consistent SQLite backup created and checked.')
            elif args.command == 'prune':
                count = store.prune(args.at or utcnow(), cfg.weather.history_retention_days, cfg.weather.poll_retention_days)
                print(canonical({'sample_chains_pruned': count}))
    except (ValueError, TypeError, OSError, sqlite3.Error, ValidationError):
        p.exit(1, 'Kári operation failed; inspect protected configuration and local health.\n')


if __name__ == '__main__':
    main()
