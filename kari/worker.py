"""One weather worker with independent cadence, health, and bounded failure buffering."""
from collections import deque
from datetime import datetime, timezone
import random
import sqlite3
import sys
import threading
import time
from uuid import uuid4

from kari import __version__
from kari.config import load_coordinates
from kari.http import FetchError, fetch_current
from kari.store import seconds
from kari.weather import normalize, stamp, utcnow

STORAGE_ERRORS = (sqlite3.Error, OSError)


class Health:
    def __init__(self, max_age=3600):
        self.max_age = max_age
        self.status = 'starting'
        self.streak = 0
        self.last_usable = None

    def evaluate(self, elapsed, evidence_age, valid_poll):
        usable = evidence_age is not None and 0 <= evidence_age <= self.max_age
        if valid_poll and usable:
            self.last_usable = elapsed
            self.streak += 1
        elif valid_poll is not None:
            self.streak = 0
        gap = elapsed if self.last_usable is None else elapsed - self.last_usable
        if self.status in ('suspect', 'unavailable') and self.streak >= 2 and usable:
            self.status = 'recovered'
        elif gap >= 10800:
            self.status = 'unavailable'
        elif gap >= 3600 or evidence_age is not None and evidence_age > self.max_age:
            self.status = 'suspect'
        elif usable and self.status not in ('suspect', 'unavailable'):
            self.status = 'healthy'
        return self.status


class Worker:
    def __init__(self, store, cfg, transport=fetch_current, clock=utcnow,
                 jitter=None, buffer_groups=100, timer=time.monotonic):
        self.store, self.cfg, self.transport, self.clock = store, cfg, transport, clock
        self.jitter = jitter or (lambda: random.uniform(0, 10))
        self.timer = timer
        if type(buffer_groups) is not int or not 1 <= buffer_groups <= 100:
            raise ValueError('Invalid bounded buffer capacity')
        self.buffer = deque(maxlen=buffer_groups)
        self.dropped = 0
        self.unreported_drops = 0
        self.lock = threading.Lock()
        self.key = 'worker:' + (cfg.device_id or 'disabled') + ':' + (cfg.location_epoch_id or 'disabled')
        self.saved = store.get_state(self.key, {})
        self.failures = self.saved.get('failures', 0)
        self.suspended = self.saved.get('suspended', False)
        self.next_due = 0
        self.anchor = None
        self.start = None
        self.health = Health(cfg.max_age_seconds)
        self.health.status = self.saved.get('health', 'starting')
        self.health.streak = self.saved.get('recovery_streak', 0)
        self.conditions = self.saved.get('conditions', {})
        self.incident_id = self.saved.get('incident_id') or str(uuid4())
        self.incident_start = self.saved.get('incident_start')
        self.latest_valid = self.saved.get('latest_valid')
        self.last_wall = self.saved.get('last_wall')
        self.storage_incident = False
        if cfg.enabled:
            self.coordinates = load_coordinates(cfg)
            self.store.bind_epoch(cfg, self.coordinates)

    def _journal(self, code):
        print('kari: ' + code, file=sys.stderr, flush=True)

    def _save(self, mono, at):
        wall = seconds(at)
        state = dict(failures=self.failures, suspended=self.suspended,
            next_at=wall + max(0, self.next_due - mono), health=self.health.status,
            cadence_at=wall + (self.anchor - mono),
            recovery_streak=self.health.streak, conditions=self.conditions,
            incident_id=self.incident_id, incident_start=self.incident_start,
            latest_valid=self.latest_valid, last_wall=wall,
            unavailable_elapsed=max(0, mono - self.start) if self.health.last_usable is None else max(0, mono - self.start - self.health.last_usable))
        self.store.set_state(self.key, state)
        self.saved = state

    def _save_health_changes(self, mono, at):
        if self.saved.get('health') != self.health.status or self.saved.get('recovery_streak') != self.health.streak:
            self._save(mono, at)

    def _health_event(self, at, code, status, scope='adapter', resolved=False):
        condition = self.conditions.get(code)
        if condition is None or (condition['status'] == 'recovered' and status != 'recovered'):
            condition = dict(incident_id=str(uuid4()), started_at=at, status=status)
        started = condition['started_at']
        # Clock incidents must themselves have a valid local envelope.
        if seconds(started) > seconds(at):
            started = at
        event = dict(schema_version='1.1.0', event_id=str(uuid4()), collection_id=str(uuid4()),
            event_type='health_event', source=dict(component='kari', collector_id=self.store.collector_id,
            adapter='system', adapter_version=__version__, collector_version=__version__),
            device_id=self.cfg.device_id, location_id=self.cfg.location_id, observed_at=None,
            collected_at=at, persisted_at=at, timestamp_basis='unknown', freshness_at_collection='unknown',
            max_age_seconds=self.cfg.max_age_seconds,
            data=dict(incident_id=condition['incident_id'], scope=scope, condition_code=code, status=status,
                      severity='info' if status in ('healthy', 'recovered', 'starting') else 'warning',
                      started_at=started, resolved_at=at if resolved else None,
                      summary='Weather worker: ' + code.replace('_', ' ')))
        self.store.record_health(event)
        self.conditions[code] = dict(condition, status=status)

    def _update_health(self, mono, at, valid_poll=None):
        age = seconds(at) - self.latest_valid if self.latest_valid is not None else None
        previous = self.health.status
        status = self.health.evaluate(mono - self.start, age, valid_poll)
        if status != previous:
            if status in ('suspect', 'unavailable') and previous not in ('suspect', 'unavailable'):
                self.incident_id = str(uuid4()); self.incident_start = at
            try:
                self._health_event(at, 'weather_availability', status, resolved=status == 'recovered')
            except STORAGE_ERRORS:
                # Keep the transition outstanding until its event can be stored.
                self.health.status = previous
                raise

    def _buffer(self, item):
        if len(self.buffer) == self.buffer.maxlen:
            self.dropped += 1
            self.unreported_drops += 1
            # Keep oldest evidence; new evidence is explicitly counted as lost.
        else:
            self.buffer.append(item)
        self.storage_incident = True
        self._journal('weather_storage_unavailable')

    def _flush(self, at):
        while self.buffer:
            result, received, cid = self.buffer[0]
            self.store.ingest(self.cfg, result, received, cid, persisted_at=at)
            self.buffer.popleft()
        if self.unreported_drops:
            self.store.finish_failure(self.cfg, str(uuid4()), at, 'storage_gap', gaps=self.unreported_drops)
            self.unreported_drops = 0
        if self.storage_incident:
            self._health_event(at, 'weather_storage_recovered', 'recovered', scope='storage', resolved=True)
            self.storage_incident = False

    def tick(self, mono):
        if not self.cfg.enabled:
            return 'disabled'
        if not self.lock.acquire(blocking=False):
            return 'busy'
        try:
            return self._tick(mono)
        finally:
            self.lock.release()

    def _tick(self, mono):
        at = self.clock()
        wall = seconds(at)
        if self.start is None:
            downtime = max(0, wall - self.saved.get('last_wall', wall))
            self.start = mono - self.saved.get('unavailable_elapsed', 0) - downtime
            self.next_due = mono + max(0, self.saved.get('next_at', wall) - wall)
            self.anchor = mono + (self.saved.get('cadence_at', wall) - wall)
        if (self.last_wall is not None and wall < self.last_wall) or any(wall < seconds(item[1]) for item in self.buffer):
            try:
                if self.conditions.get('weather_clock_invalid', {}).get('status') != 'suspect':
                    self._health_event(at, 'weather_clock_invalid', 'suspect')
            except STORAGE_ERRORS:
                self._journal('weather_clock_invalid')
            return 'clock_invalid'
        storage_ready = True
        try:
            self._flush(at)
            self._update_health(mono, at)
        except STORAGE_ERRORS:
            storage_ready = False
            self.storage_incident = True
            self._journal('weather_storage_unavailable')
        if self.suspended:
            try:
                self._save_health_changes(mono, at)
            except STORAGE_ERRORS:
                self._journal('weather_storage_unavailable')
            return 'suspended'
        if mono < self.next_due:
            try:
                self._save_health_changes(mono, at)
            except STORAGE_ERRORS:
                self._journal('weather_storage_unavailable')
            return 'waiting'
        cid = str(uuid4())
        scheduled = stamp(datetime.fromtimestamp(wall - max(0, mono - self.next_due), timezone.utc))
        slots = max(0, int((mono - self.anchor) // self.cfg.poll_interval_seconds) + 1)
        gaps = max(0, slots - 1)
        self.anchor += slots * self.cfg.poll_interval_seconds
        try:
            self.store.begin_poll(self.cfg, cid, at, scheduled, gaps=gaps, scheduled_count=slots)
        except STORAGE_ERRORS:
            self._journal('weather_storage_unavailable')
        outcome = 'success'
        result = None
        retry_after = None
        request_started = self.timer()
        try:
            coordinates = load_coordinates(self.cfg)
            if coordinates != self.coordinates:
                raise ValueError('Location change requires a new epoch')
            body = self.transport(self.cfg, coordinates)
            at = self.clock()  # Receipt time, never request start time.
            if seconds(at) < wall or self.last_wall is not None and seconds(at) < self.last_wall:
                raise ValueError('clock_invalid')
            result = normalize(body, self.cfg, at)
            if not storage_ready:
                raise sqlite3.OperationalError('Buffered evidence must be stored first')
            persisted = self.clock()
            if seconds(persisted) < seconds(at):
                self._buffer((result, at, cid))
                raise ValueError('clock_invalid')
            self.store.ingest(self.cfg, result, at, cid, persisted_at=persisted)
            if result.metrics:
                self.latest_valid = max(seconds(m['valid_at']) for m in result.metrics)
            valid = any(m['quality'] == 'valid' and m['valid_time_freshness'] == 'current' for m in result.metrics)
            self._update_health(mono, at, valid)
            if result.issues:
                self._health_event(at, 'weather_quality', 'suspect')
            self.failures = 0
        except FetchError as exc:
            outcome, retry_after = exc.code, exc.retry_after
            self.suspended = exc.suspend
            at = self.clock()
        except ValueError as exc:
            outcome = 'clock_invalid' if str(exc) == 'clock_invalid' else 'bad_response'
            # Coordinate validation errors happen before transport and require correction.
            if result is None and str(exc).startswith(('Protected coordinate', 'Location change')):
                outcome, self.suspended = 'suspended', True
        except STORAGE_ERRORS:
            outcome = 'storage_error'
            if result is not None:
                self._buffer((result, at, cid))
            else:
                self.unreported_drops += 1; self.dropped += 1
                self._journal('weather_storage_unavailable')
        finished_mono = mono + max(0, self.timer() - request_started)
        if outcome not in ('success', 'storage_error'):
            self.failures += 1
            try:
                if not any(item[2] == cid for item in self.buffer):
                    self.store.finish_failure(self.cfg, cid, at, outcome, transport_success=outcome in ('bad_response', 'clock_invalid'))
                self._update_health(mono, at, False)
                if outcome in ('clock_invalid', 'suspended'):
                    self._health_event(at, 'weather_' + outcome, 'suspect')
            except STORAGE_ERRORS:
                self.unreported_drops += 1; self.dropped += 1
                self._journal('weather_storage_unavailable')
        if outcome in ('success', 'storage_error'):
            self.next_due = self.anchor + self.jitter()
        else:
            delay = min(3600, 30 * 2 ** min(self.failures - 1, 7) + self.jitter())
            self.next_due = finished_mono + max(delay, retry_after or 0)
        self.last_wall = seconds(at)
        try:
            self._save(finished_mono, at)
        except STORAGE_ERRORS:
            self._journal('weather_storage_unavailable')
        return outcome
