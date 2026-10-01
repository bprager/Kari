import copy
import tempfile
import unittest
from pathlib import Path

from kari.monitor import render, export


REPORT = dict(phase='running', acceptance='in_progress',
              reported_at='2026-10-01T05:00:00Z', started_at='2026-10-01T04:16:10Z',
              ends_at='2026-10-08T04:16:10Z', backup=dict(export_verified=True),
              polls=dict(attempted=3, successful=3),
              metrics={'temperature': dict(samples=3, stale=0, suspect=0, coverage_fraction=.99)})


class MonitorTests(unittest.TestCase):
    def test_running_and_private_fields_are_not_exported(self):
        report = copy.deepcopy(REPORT)
        report['latitude'] = 'PRIVATE'
        report['metrics']['PRIVATE'] = dict(samples=5)
        result = render(report)
        self.assertIn('kari_weather_pilot_report_available 1\n', result)
        self.assertIn('kari_weather_pilot_phase 1\n', result)
        self.assertIn('kari_weather_pilot_backup_verified 1\n', result)
        self.assertIn('kari_weather_pilot_samples{metric="temperature"} 3\n', result)
        self.assertNotIn('PRIVATE', result)

    def test_completed_and_waiting_are_distinct(self):
        completed = dict(REPORT, phase='completed', acceptance='needs_review')
        self.assertIn('kari_weather_pilot_phase 2\n', render(completed))
        self.assertIn('kari_weather_pilot_assessment 3\n', render(completed))
        waiting = dict(phase='awaiting_configuration', acceptance='not_started',
                       reported_at=REPORT['reported_at'], started_at=None, ends_at=None)
        result = render(waiting)
        self.assertIn('kari_weather_pilot_phase 0\n', result)
        self.assertNotIn('kari_weather_pilot_ends_timestamp_seconds', result)

    def test_missing_and_malformed_reports_remove_old_success(self):
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder)/'report.json', Path(folder)/'pilot.prom'
            target.write_text('old-success')
            export(source, target)
            self.assertEqual(target.read_text(), 'kari_weather_pilot_report_available 0\n')
            for content in ('{', 'null', '{"phase":"running"}'):
                source.write_text(content)
                export(source, target)
                self.assertEqual(target.read_text(), 'kari_weather_pilot_report_available 0\n')

    def test_nonfinite_and_string_metrics_rejected(self):
        for value in (float('nan'), float('inf'), 'injected\nmetric 1', -1):
            report = copy.deepcopy(REPORT)
            report['polls']['attempted'] = value
            with self.assertRaises(ValueError):
                render(report)


class DashboardTests(unittest.TestCase):
    def test_existing_panels_preserved_and_repeated_update_is_identical(self):
        import importlib.util
        path = Path(__file__).resolve().parents[1]/'deploy/monitoring/update_dashboard.py'
        spec = importlib.util.spec_from_file_location('dashboard_update', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        original = dict(id=1, title='Existing sensor', type='stat', gridPos=dict(x=0,y=0,w=24,h=8))
        dashboard = dict(uid='kari-operations', panels=[copy.deepcopy(original)])
        once = module.update(dashboard)
        self.assertEqual(once['panels'][0], original)
        self.assertEqual(module.update(copy.deepcopy(once)), once)
        ids = [p['id'] for p in once['panels']]
        self.assertEqual(len(ids), len(set(ids)))
