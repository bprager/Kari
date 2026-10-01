import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1]/'deploy/monitoring/terminal_pilot.py'
spec = importlib.util.spec_from_file_location('terminal_pilot', PATH)
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)


class TerminalPilotTests(unittest.TestCase):
    def metrics(self):
        return {('phase',''):1, ('assessment',''):1, ('report_available',''):1,
                ('report_timestamp_seconds',''):1000, ('started_timestamp_seconds',''):900,
                ('ends_timestamp_seconds',''):605700, ('backup_verified',''):1,
                ('polls_successful',''):2, ('polls_attempted',''):2,
                ('samples','temperature'):2, ('coverage_fraction','temperature'):.9}

    def test_current_and_stale(self):
        current = '\n'.join(text for text, _ in pilot.render(self.metrics(), now=1100))
        self.assertIn('RUNNING',current)
        self.assertIn('IN PROGRESS',current)
        self.assertIn('verified',current)
        self.assertIn('90.0%',current)
        stale = '\n'.join(text for text,_ in pilot.render(self.metrics(),now=2301))
        self.assertIn('STALE',stale)

    def test_absent_and_failed_cached_response(self):
        self.assertIn('UNAVAILABLE',pilot.render({},now=1100)[1][0])
        text='\n'.join(t for t,_ in pilot.render(self.metrics(),now=1100,unreachable=True))
        self.assertIn('UNREACHABLE',text)
        self.assertIn('cached',text)

    def test_allowlist_and_invalid_numbers(self):
        data={'status':'success','data':{'result':[
            {'metric':{'__name__':'kari_weather_pilot_phase'},'value':[0,'1']},
            {'metric':{'__name__':'private_address'},'value':[0,'123']},
            {'metric':{'__name__':'kari_weather_pilot_samples','metric':'private'},'value':[0,'2']}]}}
        self.assertEqual(pilot.parse(data),{('phase',''):1})
        data['data']['result'][0]['value'][1]='NaN'
        with self.assertRaises(ValueError): pilot.parse(data)

    def test_fetch_failure_is_cached_and_retries_after_a_minute(self):
        cache=pilot.Reader()
        with patch.object(pilot,'fetch',side_effect=OSError), patch.object(pilot.time,'monotonic',side_effect=[0,10,61]):
            self.assertEqual(cache.read(),({},True))
            self.assertEqual(cache.read(),({},True))
            self.assertEqual(cache.read(),({},True))
            self.assertEqual(pilot.fetch.call_count,2)

    def test_negative_values_display_with_units_and_ranges(self):
        values = self.metrics()
        for key,value in [('latest',-2),('average_1h',-3),('average_24h',-4),('minimum_24h',-6),('maximum_24h',-1)]:
            values['value_'+key,'temperature'] = value
        text = '\n'.join(t for t,_ in pilot.render(values,now=1100))
        self.assertIn('Temperature (°C)',text)
        self.assertIn('-6.0–-1.0',text)
        self.assertIn('AVG 1h',text)
        self.assertIn('available history only',text)
        data={'status':'success','data':{'result':[{'metric':{'__name__':'kari_weather_pilot_value_latest','metric':'temperature'},'value':[0,'-2']}]}}
        self.assertEqual(pilot.parse(data)[('value_latest','temperature')],-2)
