import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from kari.config import WeatherConfig
from kari.store import Store
from kari.weather import normalize
from test_weather import CONFIG, FIXTURE, NOW


class PilotTests(unittest.TestCase):
    def setUp(self):
        import importlib.util
        self.assertIsNotNone(importlib.util.find_spec('kari.pilot'), 'pilot automation is required')
        from kari.pilot import Pilot
        self.Pilot = Pilot
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.config = self.folder / 'config.yaml'
        self.config.write_text('version: 2\nadapters:\n  weather:\n    enabled: false\n')
        self.state = self.folder / 'pilot.json'
        self.pilot = Pilot(self.config, self.state, self.folder / 'reports')

    def test_missing_location_waits_without_network_or_start(self):
        self.pilot.step(0, NOW)
        state = json.loads(self.state.read_text())
        self.assertEqual(state['phase'], 'awaiting_configuration')
        self.assertIsNone(state['started_at'])
        self.assertIsNone(state['ends_at'])

    def populate(self):
        database = self.folder / 'weather.sqlite3'
        self.config.write_text('version: 2\ncollector:\n  database_path: ' + str(database) + '\nadapters:\n  weather:\n    enabled: false\n    device_id: weather-01\n    location_id: location-01\n    location_epoch_id: epoch-01\n')
        cfg = WeatherConfig.from_mapping(CONFIG)
        with Store(database) as store:
            result = normalize(FIXTURE,cfg,NOW)
            store.ingest(cfg,result,NOW,str(uuid4()),persisted_at=NOW)
        return database

    def test_deadline_survives_restart_and_stops_exactly(self):
        self.populate()
        self.pilot.begin(NOW)
        state = json.loads(self.state.read_text())
        self.assertEqual(state['ends_at'],'2026-10-07T21:00:05Z')
        restarted = self.Pilot(self.config,self.state,self.folder/'reports')
        restarted.begin('2026-10-01T21:00:05Z')
        self.assertEqual(restarted.state['ends_at'],state['ends_at'])
        with patch('kari.pilot.Worker') as worker:
            restarted.step(0,state['ends_at'])
            worker.assert_not_called()
        self.assertEqual(restarted.state['phase'],'completed')

    def test_report_validates_export_and_restores_backup(self):
        self.populate()
        self.pilot.begin(NOW)
        report = self.pilot.report('2026-09-30T21:15:05Z')
        self.assertEqual(report['events'],5)
        self.assertEqual(report['polls']['attempted'],1)
        self.assertEqual(report['backup']['integrity'],'ok')
        self.assertTrue(report['backup']['export_verified'])
        self.assertEqual(report['metrics']['temperature']['samples'],1)
        self.assertTrue((self.folder/'reports/latest.json').exists())
        self.assertTrue((self.folder/'reports/latest.md').exists())
        self.assertNotIn('latitude',json.dumps(report))
        again = self.pilot.report('2026-09-30T21:30:05Z')
        self.assertEqual(again['backup']['file'],report['backup']['file'])

    def test_final_report_cannot_claim_success_without_coverage(self):
        self.populate()
        self.pilot.begin(NOW)
        final = self.pilot.report('2026-10-07T21:00:05Z')
        self.assertEqual(final['phase'],'completed')
        self.assertEqual(final['acceptance'],'needs_review')
        self.assertTrue((self.folder/'reports/final.json').exists())

    def test_report_without_start_is_explicitly_waiting(self):
        self.pilot.step(0,NOW)
        report = self.pilot.report(NOW)
        self.assertEqual(report['phase'],'awaiting_configuration')
        self.assertIsNone(report['started_at'])

    def test_controller_verifies_first_response_and_starts_automatically(self):
        import yaml
        from kari.worker import Worker
        coords=self.folder/'coordinates.json'
        coords.write_text(json.dumps(dict(latitude=0,longitude=0,approved_latitude=0,
            approved_longitude=0,approved_epoch_id='epoch-01',coordinate_sharing_approved=True)))
        coords.chmod(0o600)
        config=dict(version=2,collector=dict(database_path=str(self.folder/'live.sqlite3')),
                    adapters=dict(weather=dict(CONFIG,enabled=True,coordinates_file=str(coords),
                        eligible_use_confirmed=True,current_semantics_verified=True)))
        self.config.write_text(yaml.safe_dump(config))
        def create(store,cfg,**kwargs):
            return Worker(store,cfg,transport=lambda *_: copy.deepcopy(FIXTURE),clock=lambda: NOW,
                          jitter=lambda:0,timer=lambda:0,**kwargs)
        try:
            with patch('kari.pilot.Worker',side_effect=create):
                self.assertEqual(self.pilot.step(0,NOW),'success')
            self.assertEqual(self.pilot.state['phase'],'running')
            self.assertEqual(self.pilot.state['started_at'],NOW)
            self.assertEqual(self.pilot.state['provider_verification']['metrics'],5)
            self.assertEqual(self.pilot.state['provider_verification']['solar_interval_seconds'],900)
        finally:
            self.pilot.close()

    def test_incomplete_backup_is_never_reported_verified(self):
        self.populate(); self.pilot.begin(NOW)
        self.pilot.reports.mkdir(parents=True,exist_ok=True)
        (self.pilot.reports/'final-backup.sqlite3').touch()
        report=self.pilot.report('2026-10-07T21:00:05Z')
        self.assertEqual(report['backup']['events'],5)
        self.assertTrue(report['backup']['export_verified'])
        self.assertTrue((self.pilot.reports/'final-backup.sqlite3.manifest.json').exists())

    def test_corrupt_backup_rebuilt_from_snapshot(self):
        self.populate(); self.pilot.begin(NOW)
        report=self.pilot.report('2026-09-30T21:15:05Z')
        backup=self.pilot.reports/report['backup']['file']
        backup.write_bytes(b'broken')
        report=self.pilot.report('2026-09-30T21:30:05Z')
        self.assertEqual(report['backup']['events'],5)
        self.assertTrue(report['backup']['export_verified'])

    def test_stale_samples_cannot_inflate_acceptance_coverage(self):
        database=self.populate(); self.pilot.begin(NOW)
        cfg=WeatherConfig.from_mapping(CONFIG)
        payload=copy.deepcopy(FIXTURE)
        payload['current']['time'] += 900
        at='2026-09-30T23:15:05Z'
        with Store(database) as store:
            store.ingest(cfg,normalize(payload,cfg,at),at,str(uuid4()),persisted_at=at)
        report=self.pilot.report('2026-10-01T00:00:05Z')
        self.assertEqual(report['metrics']['temperature']['stale'],1)
        self.assertLessEqual(report['metrics']['temperature']['coverage_fraction'],900/10800)
