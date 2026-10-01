import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/open_meteo_current.json').read_text())
NOW = '2026-09-30T21:00:05Z'
CONFIG = dict(enabled=False, provider='open_meteo', location_id='location-01',
              location_epoch_id='epoch-01', device_id='weather-01', display_timezone='America/Los_Angeles')


class WeatherTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('kari.weather'), 'weather normalizer is required')
        from kari.config import WeatherConfig
        from kari.weather import normalize
        self.config = WeatherConfig.from_mapping(CONFIG)
        self.normalize = normalize

    def test_normalization_and_privacy(self):
        payload = copy.deepcopy(FIXTURE)
        payload.update(latitude=12.345678, longitude=23.456789, secret='never-export')
        result = self.normalize(payload, self.config, NOW)
        self.assertEqual(len(result.metrics), 5)
        self.assertEqual(result.missing, [])
        text = json.dumps(result.metrics)
        for forbidden in ('latitude', 'longitude', '12.345678', 'never-export', 'https:'):
            self.assertNotIn(forbidden, text)
        solar = result.metrics[-1]
        self.assertEqual(solar['temporal_support']['interval_start'], '2026-09-30T20:45:00Z')
        self.assertIsNone(solar['provenance']['model_id'])

    def test_missing_invalid_and_units_preserve_siblings(self):
        for variable, value in [('relative_humidity_2m', 101), ('wind_speed_10m', -1),
                                ('temperature_2m', None), ('dew_point_2m', float('inf')),
                                ('shortwave_radiation', True)]:
            with self.subTest(variable=variable):
                payload = copy.deepcopy(FIXTURE)
                payload['current'][variable] = value
                result = self.normalize(payload, self.config, NOW)
                self.assertEqual(len(result.metrics), 4)
                self.assertEqual(len(result.missing), 1)
        payload = copy.deepcopy(FIXTURE)
        payload['current_units']['wind_speed_10m'] = 'km/h'
        self.assertEqual(len(self.normalize(payload, self.config, NOW).metrics), 4)

    def test_interval_never_inferred_and_future_time_rejected(self):
        for interval in (None, 0, -1, True, '900'):
            payload = copy.deepcopy(FIXTURE)
            payload['current']['interval'] = interval
            result = self.normalize(payload, self.config, NOW)
            self.assertEqual(result.metrics, [])
            self.assertIn('invalid_interval', result.issues)
        payload = copy.deepcopy(FIXTURE)
        payload['current']['time'] += 900
        with self.assertRaisesRegex(ValueError, 'clock'):
            self.normalize(payload, self.config, NOW)

    def test_configurable_outdoor_plausibility(self):
        from kari.config import WeatherConfig
        config = WeatherConfig.from_mapping(dict(CONFIG, plausibility={'temperature': [-20, 40]}))
        payload = copy.deepcopy(FIXTURE)
        payload['current']['temperature_2m'] = 45
        data = self.normalize(payload, config, NOW).metrics[0]
        self.assertEqual(data['quality'], 'suspect')
        self.assertEqual(data['value'], 45)

    def test_disabled_defaults_no_secrets_and_strict_options(self):
        from kari.config import WeatherConfig, load_config
        self.assertFalse(WeatherConfig.from_mapping({}).enabled)
        for changes in ({'poll_interval_seconds': 899}, {'enabled': 'false'},
                        {'provider': 'other'}, {'raw_response_retention_days': 1},
                        {'history_retention_days': -1}, {'latitude': 12},
                        {'display_timezone': 'made/up'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                WeatherConfig.from_mapping(dict(CONFIG, **changes))
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'config.yaml'
            p.write_text('version: 2\nadapters:\n  weather:\n    enabled: false\n')
            self.assertFalse(load_config(p).weather.enabled)
            p.write_text('version: 2\nexport:\n  enabled: true\n')
            with self.assertRaises(ValueError):
                load_config(p)
            p.write_text('version: 2\nversion: 2\n')
            with self.assertRaises(ValueError):
                load_config(p)

    def test_activation_bound_to_protected_coordinates_and_epoch(self):
        from kari.config import WeatherConfig, load_coordinates
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'coordinates.json'
            values = dict(latitude=0.0, longitude=0.0, approved_latitude=0.0,
                          approved_longitude=0.0, approved_epoch_id='epoch-01',
                          coordinate_sharing_approved=True)
            p.write_text(json.dumps(values)); p.chmod(0o600)
            cfg = dict(CONFIG, enabled=True, coordinates_file=str(p),
                       eligible_use_confirmed=True, current_semantics_verified=True)
            config = WeatherConfig.from_mapping(cfg)
            self.assertEqual(load_coordinates(config), (0.0, 0.0))
            values['longitude'] = 1
            p.write_text(json.dumps(values))
            with self.assertRaises(ValueError):
                load_coordinates(config)
            p.chmod(0o644)
            with self.assertRaises(ValueError):
                load_coordinates(config)

class MalformedProviderTests(unittest.TestCase):
    def test_huge_number_and_null_metadata_do_not_crash(self):
        from kari.config import WeatherConfig
        from kari.weather import normalize
        cfg = WeatherConfig.from_mapping(CONFIG)
        body = copy.deepcopy(FIXTURE)
        body['current']['temperature_2m'] = 10 ** 1000
        self.assertEqual(len(normalize(body, cfg, NOW).metrics), 4)
        for value in (None, False, [], '0'):
            body = copy.deepcopy(FIXTURE)
            body['utc_offset_seconds'] = value
            with self.assertRaises(ValueError):
                normalize(body, cfg, NOW)
