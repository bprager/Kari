"""Bounded fixed-host transport; public failures contain only allowlisted codes."""
from datetime import timezone
from email.utils import parsedate_to_datetime
import http.client
import math
import time
from urllib.parse import urlencode

from kari.weather import VARIABLES
from scripts.validate_contract import loads


class FetchError(Exception):
    def __init__(self, code, retry_after=None, suspend=False):
        if code not in ('timeout', 'transport_error', 'rate_limit', 'server_error', 'suspended', 'bad_response'):
            code = 'transport_error'
        super().__init__(code)
        self.code, self.retry_after, self.suspend = code, retry_after, suspend


def retry_delay(value, now=None):
    if not isinstance(value, str) or len(value) > 128:
        return None
    try:
        if value.isdecimal():
            result = float(value)
        else:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            result = max(0, date.timestamp() - (time.time() if now is None else now))
        return result if math.isfinite(result) and result >= 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


def fetch_current(cfg, coordinates):
    # Never log this path, follow redirects, retain response bodies, or expose exception text.
    params = dict(latitude=coordinates[0], longitude=coordinates[1],
                  current=','.join(v[0] for v in VARIABLES), temperature_unit='celsius',
                  wind_speed_unit='ms', timezone='UTC', timeformat='unixtime', models='best_match')
    connection = http.client.HTTPSConnection('api.open-meteo.com', timeout=cfg.request_timeout_seconds)
    deadline = time.monotonic() + cfg.request_timeout_seconds
    try:
        connection.request('GET', '/v1/forecast?' + urlencode(params),
                           headers={'Accept': 'application/json', 'User-Agent': 'Kari/0.1.0'})
        response = connection.getresponse()
        if response.status in (408, 429) or 500 <= response.status <= 599:
            code = 'timeout' if response.status == 408 else 'rate_limit' if response.status == 429 else 'server_error'
            raise FetchError(code, retry_delay(response.getheader('Retry-After')))
        if response.status != 200:
            raise FetchError('suspended', suspend=True)
        content = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FetchError('timeout')
            if connection.sock:
                connection.sock.settimeout(remaining)
            block = response.read1(min(8192, 65537 - len(content)))
            if not block:
                break
            content.extend(block)
            if len(content) > 65536:
                raise FetchError('bad_response')
        return loads(content.decode('utf-8'))
    except FetchError:
        raise
    except TimeoutError:
        raise FetchError('timeout') from None
    except (OSError, http.client.HTTPException):
        raise FetchError('transport_error') from None
    except (ValueError, UnicodeError):
        raise FetchError('bad_response') from None
    finally:
        connection.close()
