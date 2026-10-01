"""Transactional weather revisions and local integration backlog. No networking."""
from contextlib import contextmanager, closing
import copy
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

from kari import __version__
from kari.weather import utcnow
from scripts.validate_contract import validate_event, validate_ack, moment, utc_timestamp


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def seconds(value):
    if not isinstance(value, str) or not utc_timestamp(value):
        raise ValueError('Expected UTC RFC3339 timestamp')
    return moment(value).timestamp()


class Store:
    def __init__(self, path, collector_id='odin-kari'):
        self.path = Path(path)
        self.collector_id = collector_id
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        self.connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute('PRAGMA foreign_keys=ON')
        self.connection.execute('PRAGMA journal_mode=WAL')
        self.connection.execute('PRAGMA synchronous=FULL')
        # Serialize version inspection too: two first-start workers cannot both migrate.
        with self.transaction():
            version = self.connection.execute('PRAGMA user_version').fetchone()[0]
            if version > 1:
                raise ValueError('Unsupported database version')
            if version == 0:
                sql = (Path(__file__).parent / 'migrations/001_weather.sql').read_text()
                statement = ''
                for line in sql.splitlines(True):
                    statement += line
                    if sqlite3.complete_statement(statement):
                        self.connection.execute(statement)
                        statement = ''

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @contextmanager
    def transaction(self):
        self.connection.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.connection.execute('COMMIT')
        except BaseException:
            self.connection.execute('ROLLBACK')
            raise

    def bind_epoch(self, cfg, coordinates):
        with self.transaction():
            old = self.connection.execute('SELECT location_id, latitude, longitude FROM epochs WHERE epoch_id=?',
                                          (cfg.location_epoch_id,)).fetchone()
            expected = (cfg.location_id, *coordinates)
            if old and (old['location_id'] != cfg.location_id or old['latitude'] is not None and tuple(old) != expected):
                raise ValueError('Location change requires a new epoch')
            self.connection.execute('INSERT OR IGNORE INTO epochs VALUES (?,?,?,?)',
                                    (cfg.location_epoch_id, *expected))
            self.connection.execute('UPDATE epochs SET latitude=?,longitude=? WHERE epoch_id=? AND latitude IS NULL',
                                    (*coordinates, cfg.location_epoch_id))

    def envelope(self, cfg, collection_id, collected_at, persisted_at, data):
        return dict(schema_version='1.1.0', event_id=str(uuid4()), collection_id=collection_id,
                    event_type='weather_context', source=dict(component='kari', collector_id=self.collector_id,
                    adapter='open_meteo', adapter_version=__version__, collector_version=__version__),
                    device_id=cfg.device_id, location_id=cfg.location_id, observed_at=None,
                    collected_at=collected_at, persisted_at=persisted_at, timestamp_basis='unknown',
                    freshness_at_collection='unknown', max_age_seconds=cfg.max_age_seconds, data=data)

    def _write_event(self, event):
        validate_event(event)
        payload = canonical(event)
        size = len(payload.encode())
        if size > 16384:
            raise ValueError('Event exceeds 16 KiB')
        self.connection.execute('INSERT INTO events(event_id,schema_version,event_type,collected_at,payload,payload_bytes) VALUES (?,?,?,?,?,?)',
                                (event['event_id'], event['schema_version'], event['event_type'],
                                 seconds(event['collected_at']), payload, size))

    def record_health(self, event):
        with self.transaction():
            self._write_event(event)

    def begin_poll(self, cfg, collection_id, attempted_at, scheduled_at=None, gaps=0, scheduled_count=1):
        with self.transaction():
            self.connection.execute('INSERT OR IGNORE INTO polls(collection_id,device_id,scheduled_at,attempted_at,outcome,gaps,scheduled_count) VALUES (?,?,?,?,?,?,?)',
                                    (collection_id, cfg.device_id, seconds(scheduled_at or attempted_at),
                                     seconds(attempted_at), 'attempting', gaps, scheduled_count))

    def finish_failure(self, cfg, collection_id, at, outcome, transport_success=False, gaps=0):
        if outcome not in ('timeout', 'transport_error', 'rate_limit', 'server_error', 'suspended',
                           'bad_response', 'clock_invalid', 'storage_gap'):
            raise ValueError('Unknown sanitized poll outcome')
        with self.transaction():
            self.connection.execute('INSERT OR IGNORE INTO polls(collection_id,device_id,scheduled_at,attempted_at,outcome,gaps) VALUES (?,?,?,?,?,?)',
                                    (collection_id, cfg.device_id, seconds(at), seconds(at), 'attempting', gaps))
            self.connection.execute("UPDATE polls SET outcome=?, received_at=?, transport_success=? WHERE collection_id=? AND outcome='attempting'",
                                    (outcome, seconds(at), int(transport_success), collection_id))
            if outcome == 'storage_gap':
                self.connection.execute('UPDATE polls SET attempted=0,scheduled_count=0 WHERE collection_id=?', (collection_id,))

    def ingest(self, cfg, normalized, collected_at, collection_id, persisted_at=None):
        persisted_at = persisted_at or utcnow()
        if seconds(persisted_at) < seconds(collected_at):
            raise ValueError('clock_invalid')
        events = []
        with self.transaction():
            self.connection.execute('INSERT OR IGNORE INTO epochs(epoch_id,location_id) VALUES (?,?)',
                                    (cfg.location_epoch_id, cfg.location_id))
            location = self.connection.execute('SELECT location_id FROM epochs WHERE epoch_id=?',
                                               (cfg.location_epoch_id,)).fetchone()
            if location['location_id'] != cfg.location_id:
                raise ValueError('Location change requires a new epoch')
            old_poll = self.connection.execute('SELECT outcome FROM polls WHERE collection_id=?', (collection_id,)).fetchone()
            if old_poll and old_poll['outcome'] != 'attempting':
                return []
            for original in normalized.metrics:
                data = copy.deepcopy(original)
                p = data['provenance']
                # Time normalization happens before this boundary; temporal support participates in identity.
                key = canonical([data['location_epoch_id'], cfg.device_id, p['provider'], p['product'],
                                 p['model_selection'], data['data_kind'], data['metric'],
                                 data['valid_at'], data['temporal_support']])
                evidence = {k: data[k] for k in ('value', 'unit', 'quality', 'quality_flags', 'provenance')}
                head = self.connection.execute('SELECT * FROM samples WHERE sample_key=?', (key,)).fetchone()
                if head and json.loads(head['evidence']) == evidence:
                    continue
                sample_id = head['sample_id'] if head else str(uuid4())
                revision = head['latest_revision'] + 1 if head else 1
                data.update(sample_id=sample_id, revision=revision,
                            supersedes_event_id=head['latest_event_id'] if head else None)
                event = self.envelope(cfg, collection_id, collected_at, persisted_at, data)
                self.connection.execute('INSERT INTO samples VALUES (?,?,?,?,?) ON CONFLICT(sample_key) DO UPDATE SET latest_revision=excluded.latest_revision, latest_event_id=excluded.latest_event_id, evidence=excluded.evidence',
                                        (sample_id, key, revision, event['event_id'], canonical(evidence)))
                self._write_event(event)
                self.connection.execute('INSERT INTO weather_details VALUES (?,?,?,?,?,?)',
                                        (event['event_id'], sample_id, revision, data['location_epoch_id'],
                                         data['metric'], seconds(data['valid_at'])))
                events.append(event)
            latest = max((seconds(m['valid_at']) for m in normalized.metrics), default=None)
            outcome = 'success' if normalized.metrics else 'no_usable_metrics'
            self.connection.execute('INSERT OR IGNORE INTO polls(collection_id,device_id,scheduled_at,attempted_at,outcome) VALUES (?,?,?,?,?)',
                                    (collection_id, cfg.device_id, seconds(collected_at), seconds(collected_at), 'attempting'))
            self.connection.execute('UPDATE polls SET outcome=?,received_at=?,transport_success=1,latest_valid_at=?,missing=?,issues=? WHERE collection_id=?',
                                    (outcome, seconds(collected_at), latest, canonical(normalized.missing),
                                     canonical(normalized.issues), collection_id))
            # No publisher exists; every absent outbox row is durable, protected catch-up history.
        return events

    def mark_interrupted(self, device_id):
        with self.transaction():
            self.connection.execute("UPDATE polls SET outcome='interrupted',gaps=gaps+1 WHERE device_id=? AND outcome='attempting'", (device_id,))

    def iter_query(self, start=None, end=None, as_of=None, metric=None, epoch=None, revisions=False):
        clauses, params = [], []
        for clause, value in [('w.valid_at >= ?', start), ('w.valid_at < ?', end), ('e.collected_at <= ?', as_of)]:
            if value is not None:
                clauses.append(clause); params.append(seconds(value))
        for clause, value in [('w.metric = ?', metric), ('w.location_epoch_id = ?', epoch)]:
            if value is not None:
                clauses.append(clause); params.append(value)
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        # Rank after the as-of cutoff, so later corrections never replace past knowledge.
        sql = '''WITH selected AS (
            SELECT e.payload, e.seq, w.valid_at, w.metric, w.revision,
            ROW_NUMBER() OVER (PARTITION BY w.sample_id ORDER BY w.revision DESC) AS rank
            FROM events e JOIN weather_details w USING(event_id)''' + where + ') SELECT payload FROM selected'
        if not revisions:
            sql += ' WHERE rank=1'
        sql += ' ORDER BY valid_at, seq'
        for row in self.connection.execute(sql, params):
            yield json.loads(row[0])

    def query(self, **filters):
        return list(self.iter_query(**filters))

    def latest(self):
        # Rank inside SQLite; diagnostics must not load years of event payloads.
        sql = '''WITH ranked AS (
            SELECT e.payload, ROW_NUMBER() OVER (
                PARTITION BY w.location_epoch_id, w.metric, json_extract(e.payload,'$.device_id')
                ORDER BY w.valid_at DESC, w.revision DESC, e.seq DESC) AS rank
            FROM weather_details w JOIN events e USING(event_id)
        ) SELECT payload FROM ranked WHERE rank=1'''
        return [json.loads(row[0]) for row in self.connection.execute(sql)]

    def export(self, accepted_versions, **filters):
        versions = set(accepted_versions)
        if not versions or versions - {'1.0.0', '1.1.0'}:
            raise ValueError('Unsupported accepted version')
        for event in self.iter_query(revisions=True, **filters):
            if event['schema_version'] in versions:
                yield canonical(event)

    def enroll(self, accepted_versions, max_events=100000, max_bytes=268435456):
        versions = set(accepted_versions)
        if not versions or versions - {'1.0.0', '1.1.0'}:
            raise ValueError('Unsupported accepted version')
        if type(max_events) is not int or type(max_bytes) is not int or max_events < 1 or max_bytes < 1:
            raise ValueError('Invalid outbox capacity')
        added = 0
        with self.transaction():
            count, size = self.connection.execute("SELECT COUNT(*),COALESCE(SUM(payload_bytes),0) FROM events JOIN outbox USING(event_id) WHERE status='pending'").fetchone()
            rows = self.connection.execute('SELECT e.seq,e.event_id,e.schema_version,e.payload_bytes FROM events e LEFT JOIN outbox o USING(event_id) WHERE o.event_id IS NULL ORDER BY e.seq')
            for row in rows:
                if row['schema_version'] not in versions:
                    continue
                if count >= max_events or size + row['payload_bytes'] > max_bytes:
                    self._set_state('outbox_capacity', True)
                    break
                self.connection.execute("INSERT INTO outbox(event_id,status) VALUES (?,'pending')", (row['event_id'],))
                count += 1; size += row['payload_bytes']; added += 1
            else:
                self._set_state('outbox_capacity', False)
            cursor = self.connection.execute('SELECT MIN(seq) FROM events e LEFT JOIN outbox o USING(event_id) WHERE o.event_id IS NULL').fetchone()[0]
            self._set_state('catch_up_cursor', cursor)
        return added

    def pending(self):
        return [json.loads(r[0]) for r in self.connection.execute("SELECT payload FROM events JOIN outbox USING(event_id) WHERE status='pending' ORDER BY seq")]

    def acknowledge(self, event_id, ack):
        validate_ack(ack, event_id)
        with self.transaction():
            row = self.connection.execute('SELECT status FROM outbox WHERE event_id=?', (event_id,)).fetchone()
            if not row or row[0] == 'rejected':
                raise ValueError('Event is not eligible for acknowledgement')
            self.connection.execute("UPDATE outbox SET status='acked',acknowledgement=? WHERE event_id=?", (canonical(ack), event_id))

    def _set_state(self, key, value):
        self.connection.execute('INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, canonical(value)))

    def set_state(self, key, value):
        with self.transaction():
            self._set_state(key, value)

    def get_state(self, key, default=None):
        row = self.connection.execute('SELECT value FROM metadata WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def poll_summary(self):
        row = self.connection.execute('''SELECT COALESCE(SUM(attempted),0) AS attempted,
            COALESCE(SUM(scheduled_count),0) AS scheduled,
            COALESCE(SUM(gaps),0) AS gaps,
            COALESCE(SUM(outcome='success'),0) AS successful,
            COALESCE(SUM(transport_success),0) AS transport_successful,
            MAX(CASE WHEN transport_success=1 THEN received_at END) AS last_transport_success,
            MAX(latest_valid_at) AS latest_valid_at FROM polls''').fetchone()
        result = dict(row)
        result['missing_metrics'] = sum(len(json.loads(r[0])) for r in self.connection.execute('SELECT missing FROM polls'))
        result['integration_pending'] = self.connection.execute("SELECT COUNT(*) FROM events e LEFT JOIN outbox o USING(event_id) WHERE o.status IS NULL OR o.status!='acked'").fetchone()[0]
        result['outbox_capacity'] = self.get_state('outbox_capacity', False)
        return result

    def prune(self, now, history_days=730, poll_days=90):
        deleted = 0
        with self.transaction():
            if history_days is not None:
                if type(history_days) is not int or history_days < 1:
                    raise ValueError('Invalid history retention')
                cutoff = seconds(now) - history_days * 86400
                # Protect full chains if even one revision remains unenrolled/pending/rejected.
                samples = self.connection.execute('''SELECT w.sample_id FROM weather_details w
                    JOIN events e USING(event_id) LEFT JOIN outbox o USING(event_id)
                    GROUP BY w.sample_id HAVING MAX(w.valid_at) < ? AND MAX(e.collected_at) < ?
                    AND SUM(CASE WHEN o.status='acked' THEN 0 ELSE 1 END)=0''', (cutoff, cutoff)).fetchall()
                for row in samples:
                    self.connection.execute('DELETE FROM events WHERE event_id IN (SELECT event_id FROM weather_details WHERE sample_id=?)', (row[0],))
                    self.connection.execute('DELETE FROM samples WHERE sample_id=?', (row[0],))
                    deleted += 1
            if type(poll_days) is not int or poll_days < 1:
                raise ValueError('Invalid poll retention')
            self.connection.execute('DELETE FROM polls WHERE attempted_at < ?', (seconds(now) - poll_days * 86400,))
        return deleted

    def backup(self, destination):
        destination = Path(destination)
        if destination.resolve() == self.path.resolve() or destination.exists():
            raise ValueError('Backup destination must be a new file')
        fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        os.close(fd)
        with closing(sqlite3.connect(destination)) as target:
            self.connection.backup(target)
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Backup integrity check failed')
