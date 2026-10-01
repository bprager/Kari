import copy
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from kari.config import WeatherConfig
from kari.store import Store
from test_weather import CONFIG, FIXTURE, NOW


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('kari.worker'), 'isolated worker is required')
        from kari.worker import Worker
        self.Worker = Worker
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        path = Path(self.tmp.name)
        coords = path / 'coordinates.json'
        coords.write_text(json.dumps(dict(latitude=0, longitude=0, approved_latitude=0,
            approved_longitude=0, approved_epoch_id='epoch-01', coordinate_sharing_approved=True)))
        coords.chmod(0o600)
        self.cfg = WeatherConfig.from_mapping(dict(CONFIG, enabled=True, coordinates_file=str(coords),
            eligible_use_confirmed=True, current_semantics_verified=True))
        self.store = Store(path / 'weather.sqlite3'); self.addCleanup(self.store.close)
        self.calls = []
        def transport(cfg, coordinates):
            self.calls.append(True)
            return copy.deepcopy(FIXTURE)
        self.transport = transport

    def worker(self, transport=None, **kwargs):
        return self.Worker(self.store, self.cfg, transport=transport or self.transport,
                           jitter=lambda: 0, clock=lambda: NOW, timer=lambda: 0, **kwargs)

    def test_disabled_adapter_makes_no_calls(self):
        worker = self.Worker(self.store, WeatherConfig(), transport=self.transport)
        self.assertEqual(worker.tick(0), 'disabled')
        self.assertEqual(self.calls, [])

    def test_cadence_duplicate_and_no_cached_acquisition(self):
        worker = self.worker()
        self.assertEqual(worker.tick(0), 'success')
        self.assertEqual(worker.tick(10), 'waiting')
        self.assertEqual(worker.tick(900), 'success')
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(self.store.query()), 5)
        self.assertEqual(self.store.poll_summary()['attempted'], 2)

    def test_backoff_retry_after_and_permanent_suspension(self):
        from kari.http import FetchError
        failure = [FetchError('rate_limit', retry_after=7200)]
        def transport(*args):
            raise failure[0]
        worker = self.worker(transport)
        self.assertEqual(worker.tick(0), 'rate_limit')
        self.assertGreaterEqual(worker.next_due, 7200)
        self.assertEqual(worker.tick(100), 'waiting')
        failure[0] = FetchError('suspended', suspend=True)
        self.assertEqual(worker.tick(7200), 'suspended')
        self.assertEqual(worker.tick(20000), 'suspended')
        restarted = self.worker()
        self.assertEqual(restarted.tick(0), 'suspended')
        self.assertEqual(self.calls, [])

    def test_bad_response_and_future_clock_are_sanitized(self):
        worker = self.worker(lambda *args: {'reason': 'https://secret.invalid/?latitude=12'})
        self.assertEqual(worker.tick(0), 'bad_response')
        self.assertNotIn('secret', json.dumps(self.store.poll_summary()))
        body = copy.deepcopy(FIXTURE); body['current']['time'] += 900
        worker.transport = lambda *args: body
        self.assertEqual(worker.tick(1000), 'clock_invalid')

    def test_weather_health_thresholds_and_two_poll_recovery(self):
        from kari.worker import Health
        health = Health(max_age=3600)
        self.assertEqual(health.evaluate(0, None, False), 'starting')
        self.assertEqual(health.evaluate(3599, None, False), 'starting')
        self.assertEqual(health.evaluate(3600, None, False), 'suspect')
        self.assertEqual(health.evaluate(10800, None, False), 'unavailable')
        self.assertEqual(health.evaluate(10801, 0, True), 'unavailable')
        self.assertEqual(health.evaluate(10802, 0, True), 'recovered')
        self.assertEqual(health.evaluate(10803, 0, True), 'healthy')
        self.assertEqual(health.evaluate(15004, 4201, False), 'suspect')

    def test_storage_outage_is_bounded_and_replayed(self):
        worker = self.worker(buffer_groups=1)
        original = self.store.ingest
        with patch.object(self.store, 'ingest', side_effect=sqlite3.OperationalError('disk full private URL')):
            self.assertEqual(worker.tick(0), 'storage_error')
            self.assertEqual(worker.tick(900), 'storage_error')
        self.assertEqual(len(worker.buffer), 1)
        self.assertEqual(worker.dropped, 1)
        self.assertEqual(worker.tick(1800), 'success')
        self.assertEqual(len(worker.buffer), 0)
        self.assertEqual(len(self.store.query()), 5)
        self.assertGreaterEqual(self.store.poll_summary()['gaps'], 1)

    def test_restart_respects_persisted_schedule(self):
        self.worker().tick(0)
        restarted = self.worker()
        self.assertEqual(restarted.tick(0), 'waiting')
        self.assertEqual(len(self.calls), 1)


class HttpTests(unittest.TestCase):
    def test_http_module_exists(self):
        self.assertIsNotNone(importlib.util.find_spec('kari.http'), 'bounded HTTP transport is required')

    def test_retry_after_seconds_and_date(self):
        from kari.http import retry_delay
        self.assertEqual(retry_delay('7200', now=0), 7200)
        self.assertEqual(retry_delay('Thu, 01 Jan 1970 02:00:00 GMT', now=0), 7200)
        self.assertIsNone(retry_delay('https://private'))
        self.assertIsNone(retry_delay('-1'))

class HealthDurabilityTests(unittest.TestCase):
    setUp = WorkerTests.setUp
    worker = WorkerTests.worker
    def test_suspended_health_is_persisted_without_requests(self):
        from kari.http import FetchError
        def transport(*args):
            raise FetchError('suspended', suspend=True)
        worker = self.worker(transport)
        worker.tick(0)
        worker.tick(10800)
        self.assertEqual(self.store.get_state(worker.key)['health'], 'unavailable')

    def test_failed_health_event_retries_transition(self):
        worker = self.worker()
        with patch.object(self.store, 'record_health', side_effect=sqlite3.OperationalError('full')):
            worker.tick(0)
        worker.tick(900)
        rows = self.store.connection.execute("SELECT payload FROM events WHERE event_type='health_event'").fetchall()
        self.assertTrue(any(json.loads(r[0])['data']['condition_code'] == 'weather_availability' for r in rows))

    def test_recovery_streak_survives_restart(self):
        worker = self.worker()
        worker.health.status = 'unavailable'
        worker.tick(0)
        self.assertEqual(worker.health.streak, 1)
        restarted = self.worker()
        restarted.tick(0)
        restarted.tick(900)
        self.assertEqual(restarted.health.status, 'recovered')

    def test_storage_and_availability_have_different_incidents(self):
        worker = self.worker()
        worker.tick(0)
        worker._health_event(NOW, 'weather_storage_recovered', 'recovered', scope='storage', resolved=True)
        rows = [json.loads(r[0]) for r in self.store.connection.execute("SELECT payload FROM events WHERE event_type='health_event'")]
        ids = {r['data']['incident_id'] for r in rows}
        self.assertGreaterEqual(len(ids), 2)

class HttpBehaviorTests(unittest.TestCase):
    def response(self, status=200, payload=None, retry=None):
        from unittest.mock import MagicMock
        response = MagicMock()
        response.status = status
        response.getheader.return_value = retry
        response.read1.side_effect = [json.dumps(payload or FIXTURE).encode(), b'']
        connection = MagicMock()
        connection.getresponse.return_value = response
        return connection

    def test_success_request_is_current_utc_and_units_explicit(self):
        from kari.http import fetch_current
        cfg = WeatherConfig()
        conn = self.response()
        with patch('kari.http.http.client.HTTPSConnection', return_value=conn):
            result = fetch_current(cfg, (0, 0))
        self.assertEqual(result, FIXTURE)
        path = conn.request.call_args.args[1]
        for field in ('current=', 'temperature_unit=celsius', 'wind_speed_unit=ms', 'timezone=UTC', 'timeformat=unixtime'):
            self.assertIn(field, path)
        conn.close.assert_called_once()

    def test_statuses_and_errors_are_sanitized(self):
        from kari.http import fetch_current, FetchError
        for status, code, suspend in [(408, 'timeout', False), (429, 'rate_limit', False),
                                      (503, 'server_error', False), (401, 'suspended', True),
                                      (400, 'suspended', True), (302, 'suspended', True)]:
            with self.subTest(status=status):
                conn = self.response(status, retry='7200')
                with patch('kari.http.http.client.HTTPSConnection', return_value=conn):
                    with self.assertRaises(FetchError) as raised:
                        fetch_current(WeatherConfig(), (0, 0))
                self.assertEqual(raised.exception.code, code)
                self.assertEqual(raised.exception.suspend, suspend)
                if not suspend:
                    self.assertEqual(raised.exception.retry_after, 7200)
                self.assertEqual(str(raised.exception), code)
                conn.close.assert_called_once()
        conn = self.response()
        conn.request.side_effect = OSError('https://private/?latitude=123')
        with patch('kari.http.http.client.HTTPSConnection', return_value=conn):
            with self.assertRaises(FetchError) as raised:
                fetch_current(WeatherConfig(), (0, 0))
        self.assertEqual(str(raised.exception), 'transport_error')

    def test_oversize_duplicate_keys_and_invalid_json(self):
        from kari.http import fetch_current, FetchError
        for content in (b'x' * 65537, b'{"a":1,"a":2}', b'{"value":NaN}', b'not json'):
            conn = self.response()
            conn.getresponse.return_value.read1.side_effect = [content, b'']
            with patch('kari.http.http.client.HTTPSConnection', return_value=conn):
                with self.assertRaises(FetchError) as raised:
                    fetch_current(WeatherConfig(), (0, 0))
            self.assertEqual(raised.exception.code, 'bad_response')


class SchedulerAccountingTests(unittest.TestCase):
    setUp = WorkerTests.setUp
    worker = WorkerTests.worker

    def test_retry_is_not_another_scheduled_poll_and_gaps_not_recounted(self):
        from kari.http import FetchError
        def transport(*args):
            raise FetchError('timeout')
        worker = self.worker(transport)
        worker.tick(0); worker.tick(30)
        self.assertEqual(self.store.poll_summary()['scheduled'], 1)
        self.assertEqual(self.store.poll_summary()['attempted'], 2)
        worker.tick(3600)
        self.assertEqual(self.store.poll_summary()['scheduled'], 5)
        self.assertEqual(self.store.poll_summary()['gaps'], 3)
        worker.tick(4000)
        self.assertEqual(self.store.poll_summary()['gaps'], 3)

    def test_one_inflight_and_other_database_reads_work(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        entered = threading.Event(); release = threading.Event()
        def transport(*args):
            entered.set(); release.wait(5)
            return copy.deepcopy(FIXTURE)
        def poll():
            with Store(self.store.path) as store:
                worker = self.Worker(store, self.cfg, transport=transport, clock=lambda: NOW, jitter=lambda: 0)
                holder.append(worker)
                return worker.tick(0)
        holder = []
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(poll)
            self.assertTrue(entered.wait(3))
            self.assertEqual(holder[0].tick(0), 'busy')
            self.assertEqual(self.store.poll_summary()['attempted'], 1)
            release.set()
            self.assertEqual(future.result(), 'success')

class ClockRollbackTests(unittest.TestCase):
    setUp = WorkerTests.setUp
    worker = WorkerTests.worker

    def test_clock_rollback_preserves_buffer_until_time_recovers(self):
        worker = self.worker()
        with patch.object(self.store, 'ingest', side_effect=sqlite3.OperationalError('full')):
            worker.tick(0)
        worker.clock = lambda: '2026-09-30T21:00:04Z'
        self.assertEqual(worker.tick(1), 'clock_invalid')
        self.assertEqual(len(worker.buffer), 1)
        worker.clock = lambda: '2026-09-30T21:00:06Z'
        worker.tick(2)
        self.assertEqual(len(worker.buffer), 0)
        self.assertEqual(len(self.store.query()), 5)

class RetryReceiptTests(unittest.TestCase):
    setUp = WorkerTests.setUp

    def test_retry_after_starts_when_failed_request_finishes(self):
        from kari.http import FetchError
        def transport(*args):
            raise FetchError('rate_limit', retry_after=7200)
        timer = iter([100, 120])
        worker = self.Worker(self.store, self.cfg, transport=transport, clock=lambda: NOW,
                             jitter=lambda: 0, timer=lambda: next(timer))
        worker.tick(0)
        self.assertEqual(worker.next_due, 7220)
