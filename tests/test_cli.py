import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def test_offline_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            config = folder / 'config.yaml'
            config.write_text('version: 2\ncollector:\n  database_path: ' + str(folder / 'weather.sqlite3') + '\nadapters:\n  weather:\n    enabled: false\n    location_id: location-01\n    location_epoch_id: epoch-01\n    device_id: weather-01\n')
            def run(*args):
                completed = subprocess.run([sys.executable, '-m', 'kari', '--config', str(config), *args],
                                           cwd=ROOT, capture_output=True, text=True)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertNotIn('Traceback', completed.stderr)
                return completed.stdout
            self.assertIn('valid', run('validate-config'))
            self.assertIn('disabled', run('run-weather', '--once'))
            result = run('import-weather', '--input', str(ROOT / 'tests/fixtures/open_meteo_current.json'), '--collected-at', '2026-09-30T21:00:05Z')
            self.assertEqual(json.loads(result)['events_created'], 5)
            replay = run('import-weather', '--input', str(ROOT / 'tests/fixtures/open_meteo_current.json'), '--collected-at', '2026-09-30T21:01:05Z')
            self.assertEqual(json.loads(replay)['events_created'], 0)
            exported = run('export-weather', '--schema-version', '1.1.0')
            self.assertEqual(len(exported.splitlines()), 5)
            self.assertEqual(run('export-weather', '--schema-version', '1.0.0'), '')
            query = json.loads(run('query-weather', '--at', '2026-09-30T23:00:00Z', '--metric', 'temperature'))
            self.assertEqual(query[0]['freshness_now'], 'stale')
            self.assertEqual(query[0]['age_seconds'], 7200)
            self.assertEqual(json.loads(run('health'))['attempted'], 2)
            self.assertIn('backup', run('backup', '--destination', str(folder / 'backup.sqlite3')))
            self.assertIn('0', run('prune', '--at', '2030-01-01T00:00:00Z'))
            self.assertEqual(exported, run('export-weather', '--schema-version', '1.1.0'))
            result = subprocess.run([sys.executable, '-m', 'kari', '--config', str(config),
                                     'import-weather', '--input', str(folder / 'private-coordinate-file'),
                                     '--collected-at', '2026-09-30T21:00:05Z'], cwd=ROOT, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('private-coordinate-file', result.stderr)
            self.assertNotIn('Traceback', result.stderr)
