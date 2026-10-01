import copy
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from kari.config import WeatherConfig
from kari.weather import normalize
from test_weather import CONFIG, FIXTURE, NOW


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('kari.store'), 'durable weather storage is required')
        from kari.store import Store
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'weather.sqlite3'
        self.Store = Store
        self.store = Store(self.path)
        self.addCleanup(self.store.close)
        self.cfg = WeatherConfig.from_mapping(CONFIG)

    def ingest(self, payload=None, at=NOW, collection_id=None):
        normalized = normalize(payload or FIXTURE, self.cfg, at)
        return self.store.ingest(self.cfg, normalized, at, collection_id or str(uuid4()), persisted_at=at)

    def test_duplicate_revision_aba_and_new_time(self):
        first = self.ingest()
        self.assertEqual(len(first), 5)
        self.assertEqual(self.ingest(at='2026-09-30T21:01:00Z'), [])
        changed = copy.deepcopy(FIXTURE)
        changed['current']['temperature_2m'] += 1
        second = self.ingest(changed, at='2026-09-30T21:02:00Z')
        third = self.ingest(at='2026-09-30T21:03:00Z')
        self.assertEqual(second[0]['data']['revision'], 2)
        self.assertEqual(third[0]['data']['revision'], 3)
        self.assertEqual(third[0]['data']['supersedes_event_id'], second[0]['event_id'])
        self.assertEqual(first[0]['data']['sample_id'], third[0]['data']['sample_id'])
        self.assertEqual(len(self.store.query()), 5)
        self.assertEqual(len(self.store.query(revisions=True)), 7)
        changed['current']['time'] += 900
        self.assertEqual(len(self.ingest(changed, at='2026-09-30T21:15:01Z')), 5)
        self.assertEqual(self.store.poll_summary()['attempted'], 5)

    def test_as_of_filters_before_revision_selection(self):
        first = self.ingest()
        changed = copy.deepcopy(FIXTURE)
        changed['current']['temperature_2m'] = 30
        self.ingest(changed, at='2026-09-30T21:30:00Z')
        old = self.store.query(as_of='2026-09-30T21:10:00Z', metric='temperature')
        self.assertEqual(old[0], first[0])
        self.assertEqual(self.store.query(metric='temperature')[0]['data']['value'], 30)
        self.assertEqual(self.store.query(start='2026-09-30T22:00:00Z'), [])

    def test_restart_concurrent_writers_and_migration_rerun(self):
        def insert(_):
            with self.Store(self.path) as store:
                return store.ingest(self.cfg, normalize(FIXTURE, self.cfg, NOW), NOW, str(uuid4()), persisted_at=NOW)
        with ThreadPoolExecutor(max_workers=4) as pool:
            counts = [len(r) for r in pool.map(insert, range(8))]
        self.assertEqual(sum(counts), 5)
        ids = [e['event_id'] for e in self.store.query()]
        with self.Store(self.path) as restarted:
            self.assertEqual([e['event_id'] for e in restarted.query()], ids)
            self.assertEqual(restarted.connection.execute('PRAGMA user_version').fetchone()[0], 1)
            self.assertEqual(restarted.connection.execute('PRAGMA journal_mode').fetchone()[0], 'wal')

    def test_acquisition_retry_and_transaction_rollback(self):
        cid = str(uuid4())
        self.ingest(collection_id=cid)
        changed = copy.deepcopy(FIXTURE)
        changed['current']['temperature_2m'] = 30
        self.assertEqual(self.ingest(changed, collection_id=cid), [])
        self.assertEqual(self.store.poll_summary()['attempted'], 1)
        invalid = normalize(FIXTURE, self.cfg, NOW)
        invalid.metrics[0]['value'] = 27
        invalid.metrics[-1]['unit'] = 'bad'
        with self.assertRaises(Exception):
            self.store.ingest(self.cfg, invalid, NOW, str(uuid4()), persisted_at=NOW)
        self.assertEqual(len(self.store.query(revisions=True)), 5)

    def test_retention_backlog_ack_and_backup(self):
        events = self.ingest()
        future = '2030-01-01T00:00:00Z'
        self.assertEqual(self.store.prune(future, 730, 90), 0)
        self.assertEqual(self.store.enroll(['1.0.0']), 0)
        self.assertEqual(self.store.enroll(['1.1.0'], max_events=2), 2)
        self.assertEqual(len(self.store.pending()), 2)
        for event in events[:2]:
            ack = dict(schema_version='1.0.0', event_id=event['event_id'], status='accepted', stored_at=NOW)
            self.store.acknowledge(event['event_id'], ack)
            self.store.acknowledge(event['event_id'], dict(ack, status='duplicate'))
        self.assertEqual(self.store.enroll(['1.1.0']), 3)
        backup = Path(self.tmp.name) / 'backup.sqlite3'
        self.store.backup(backup)
        with self.Store(backup) as restored:
            self.assertEqual(restored.query(revisions=True), self.store.query(revisions=True))
            self.assertEqual(len(restored.pending()), 3)
        self.assertEqual(self.store.prune(future, 730, 90), 2)
        for event in self.store.pending():
            self.store.acknowledge(event['event_id'], dict(schema_version='1.0.0', event_id=event['event_id'], status='duplicate', stored_at=NOW))
        self.assertEqual(self.store.prune(future, 730, 90), 3)
        self.assertEqual(self.store.query(), [])

    def test_retention_protects_entire_chain_and_unlimited(self):
        self.ingest()
        self.store.enroll(['1.1.0'])
        for event in self.store.pending():
            self.store.acknowledge(event['event_id'], dict(schema_version='1.0.0', event_id=event['event_id'], status='accepted', stored_at=NOW))
        changed = copy.deepcopy(FIXTURE)
        changed['current']['temperature_2m'] += 1
        self.ingest(changed, at='2026-09-30T21:01:00Z')
        self.assertEqual(self.store.prune('2030-01-01T00:00:00Z', None, 90), 0)
        self.assertEqual(self.store.prune('2030-01-01T00:00:00Z', 730, 90), 4)
        self.assertEqual(len(self.store.query(revisions=True)), 2)

    def test_epoch_binding_rejects_move_and_reassignment(self):
        self.store.bind_epoch(self.cfg, (0, 0))
        self.store.bind_epoch(self.cfg, (0, 0))
        with self.assertRaises(ValueError):
            self.store.bind_epoch(self.cfg, (1, 0))
        other = WeatherConfig.from_mapping(dict(CONFIG, location_id='location-02'))
        with self.assertRaises(ValueError):
            self.store.bind_epoch(other, (0, 0))
        self.assertNotIn('latitude', json.dumps(self.store.poll_summary()))

class AdditionalDurabilityTests(unittest.TestCase):
    setUp = StoreTests.setUp
    ingest = StoreTests.ingest

    def test_offline_import_cannot_reassign_existing_epoch(self):
        self.ingest()
        other = WeatherConfig.from_mapping(dict(CONFIG, location_id='location-02'))
        with self.assertRaises(ValueError):
            self.store.ingest(other, normalize(FIXTURE, other, NOW), NOW, str(uuid4()), persisted_at=NOW)

    def test_process_crash_rolls_back_and_restart_can_ingest(self):
        import subprocess
        import sys
        code = '''import json, os, sys
from kari.store import Store
from kari.config import WeatherConfig
from kari.weather import normalize
from uuid import uuid4
cfg=WeatherConfig.from_mapping(json.loads(sys.argv[2]))
store=Store(sys.argv[1])
write=store._write_event
def crash(event):
    write(event)
    os._exit(23)
store._write_event=crash
store.ingest(cfg, normalize(json.loads(sys.argv[3]),cfg,sys.argv[4]),sys.argv[4],str(uuid4()),persisted_at=sys.argv[4])
'''
        result = subprocess.run([sys.executable, '-c', code, str(self.path), json.dumps(CONFIG), json.dumps(FIXTURE), NOW], capture_output=True, text=True)
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertEqual(self.store.query(), [])
        self.assertEqual(len(self.ingest()), 5)
        self.assertEqual(self.store.connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_immutable_database_payload_and_ack_rejection(self):
        import sqlite3
        self.ingest()
        self.store.enroll(['1.1.0'], max_bytes=1)
        self.assertEqual(self.store.pending(), [])
        self.assertTrue(self.store.poll_summary()['outbox_capacity'])
        self.store.enroll(['1.1.0'])
        event = self.store.pending()[0]
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.connection.execute("UPDATE events SET payload='{}'")
        with self.assertRaises(ValueError):
            self.store.acknowledge(event['event_id'], dict(schema_version='1.0.0', event_id=str(uuid4()), status='accepted', stored_at=NOW))
        self.assertEqual(len(self.store.pending()), 5)
