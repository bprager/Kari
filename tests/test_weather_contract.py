import copy
import json
import unittest
from pathlib import Path
from jsonschema import ValidationError
from scripts import validate_contract as contract

ROOT = Path(__file__).resolve().parents[1]
ADR = (ROOT / 'docs/ADR-kari-local-weather-context.md').read_text()
WEATHER = json.loads(ADR.split('```json\n')[1].split('```')[0])


class WeatherContractTests(unittest.TestCase):
    def test_exact_dispatch_and_legacy_rejects_weather(self):
        contract.validate_event(WEATHER)
        with self.assertRaises(ValidationError):
            contract.EVENT.validate(WEATHER)
        for version in ('1.1', '1.1.1', '2.0.0'):
            changed = dict(WEATHER, schema_version=version)
            with self.assertRaises(ValueError):
                contract.validate_event(changed)

    def test_weather_rejections(self):
        changes = [
            (('data', 'value'), float('nan')),
            (('data', 'value'), True),
            (('data', 'unit'), 'F'),
            (('data', 'valid_at'), '2026-09-30T21:01:00Z'),
            (('data', 'valid_at'), '2026-09-30T14:00:00-07:00'),
            (('data', 'valid_time_freshness'), 'stale'),
            (('data', 'revision'), 0),
            (('data', 'revision'), 2),
            (('data', 'quality_flags'), ['arbitrary']),
            (('data', 'provenance', 'source_variable'), 'wind_speed_10m'),
            (('data', 'provenance', 'height_m'), 10),
            (('data', 'temporal_support', 'statistic'), 'mean'),
            (('data', 'temporal_support', 'nominal_step_seconds'), 0),
            (('data', 'latitude'), 0),
            (('location_id',), None),
            (('source', 'adapter'), 'thermopro_ble'),
            (('observed_at',), WEATHER['collected_at']),
        ]
        for path, value in changes:
            with self.subTest(path=path):
                event = copy.deepcopy(WEATHER)
                target = event
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                with self.assertRaises((ValueError, ValidationError)):
                    contract.validate_event(event)

    def test_freshness_boundary_and_dst_utc(self):
        event = copy.deepcopy(WEATHER)
        event['data']['valid_at'] = '2026-11-01T09:00:00Z'
        event['collected_at'] = event['persisted_at'] = '2026-11-01T10:00:00Z'
        contract.validate_event(event)
        event['collected_at'] = event['persisted_at'] = '2026-11-01T10:00:01Z'
        with self.assertRaises(ValueError):
            contract.validate_event(event)
        event['data']['valid_time_freshness'] = 'stale'
        contract.validate_event(event)

    def test_solar_interval_is_response_duration(self):
        event = copy.deepcopy(WEATHER)
        data = event['data']
        data.update(metric='shortwave_radiation', value=25, unit='W/m2')
        data['provenance'].update(source_variable='shortwave_radiation', height_m=None)
        data['temporal_support'] = dict(statistic='mean', nominal_step_seconds=600,
                                       interval_start='2026-09-30T20:50:00Z',
                                       interval_end=data['valid_at'])
        contract.validate_event(event)
        data['temporal_support']['nominal_step_seconds'] = 900
        with self.assertRaises(ValueError):
            contract.validate_event(event)

    def test_legacy_events_valid_in_new_version(self):
        for line in (ROOT / 'examples/events.jsonl').read_text().splitlines():
            event = json.loads(line)
            event['schema_version'] = '1.1.0'
            contract.validate_event(event)
