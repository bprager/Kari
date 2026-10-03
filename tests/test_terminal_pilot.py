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
        self.assertIn('WEATHER DATA REFRESH FAILED',text)
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
        with patch.object(pilot,'fetch',side_effect=OSError), patch.object(pilot,'fetch_direct',side_effect=OSError), patch.object(pilot.time,'monotonic',side_effect=[0,10,61]):
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

    def test_data_failure_never_claims_host_is_down(self):
        text='\n'.join(t for t,_ in pilot.render(self.metrics(),now=1100,unreachable=True))
        self.assertNotIn('ODIN UNREACHABLE',text)
        self.assertIn('WEATHER DATA',text)

    def test_http_failure_uses_direct_export(self):
        reader=pilot.Reader()
        with patch.object(pilot,'fetch',side_effect=OSError), patch.object(pilot,'fetch_direct',return_value=self.metrics()):
            self.assertEqual(reader.read(),(self.metrics(),False))
        self.assertEqual(reader.source,'direct export')

    def test_both_fail_preserves_previous_values_then_recovers(self):
        reader=pilot.Reader()
        reader.values=self.metrics()
        with patch.object(pilot,'fetch',side_effect=ValueError), patch.object(pilot,'fetch_direct',side_effect=OSError):
            self.assertEqual(reader.read(),(self.metrics(),True))
        reader.next_read=0
        with patch.object(pilot,'fetch',return_value=self.metrics()), patch.object(pilot.time,'time',return_value=1100):
            self.assertEqual(reader.read(),(self.metrics(),False))

    def test_direct_export_parse_has_same_privacy_and_numeric_rules(self):
        data=pilot.parse_text('kari_weather_pilot_phase 1\nkari_weather_pilot_value_latest{metric="temperature"} -2\nprivate 123\n')
        self.assertEqual(data,{('phase',''):1,('value_latest','temperature'):-2})
        with self.assertRaises(ValueError): pilot.parse_text('kari_weather_pilot_phase NaN\n')

    def test_stale_prometheus_uses_newer_direct_report(self):
        fresh=self.metrics(); fresh['report_timestamp_seconds','']=3000
        reader=pilot.Reader()
        with patch.object(pilot,'fetch',return_value=self.metrics()), patch.object(pilot,'fetch_direct',return_value=fresh), patch.object(pilot.time,'time',return_value=3100):
            self.assertEqual(reader.read(),(fresh,False))
            self.assertEqual(reader.source,'direct export')

    def test_background_fetch_does_not_block_and_only_one_request_runs(self):
        import threading
        entered,release=threading.Event(),threading.Event()
        def blocked():
            entered.set()
            release.wait(2)
            return self.metrics()
        reader=pilot.Reader()
        with patch.object(pilot,'fetch',side_effect=blocked), patch.object(pilot.time,'time',return_value=1100):
            try:
                self.assertEqual(reader.read(background=True),({},False))
                self.assertTrue(entered.wait(1))
                reader.next_read=0
                reader.read(background=True)
                self.assertEqual(pilot.fetch.call_count,1)
            finally:
                release.set()
                for thread in threading.enumerate():
                    if thread is not threading.current_thread() and thread.name.endswith('(_refresh)'):
                        thread.join(2)
        self.assertEqual(reader.values,self.metrics())
        self.assertFalse(reader.unreachable)

    def test_empty_or_truncated_response_uses_fallback(self):
        for broken in ({}, {('report_available',''):1}):
            reader=pilot.Reader()
            with patch.object(pilot,'fetch',return_value=broken),patch.object(pilot,'fetch_direct',return_value=self.metrics()):
                self.assertEqual(reader.read(),(self.metrics(),False))
