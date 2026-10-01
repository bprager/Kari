"""Configuration v2 weather slice; coordinate material never enters public state."""
from dataclasses import dataclass, field, fields
import math
import os
from pathlib import Path
import re
import stat
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from scripts.validate_contract import loads

ID = re.compile(r'^[a-z0-9][a-z0-9._-]{0,63}$')
DEFAULT_RANGES = {'temperature': (-90, 60), 'relative_humidity': (0, 100),
                  'dew_point': (-100, 60), 'wind_speed': (0, 100),
                  'shortwave_radiation': (0, 1500)}


def number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


@dataclass(frozen=True)
class WeatherConfig:
    enabled: bool = False
    provider: str = 'open_meteo'
    poll_interval_seconds: int = 900
    location_id: str | None = None
    location_epoch_id: str | None = None
    device_id: str | None = None
    display_timezone: str = 'UTC'
    coordinates_file: str | None = field(default=None, repr=False)
    eligible_use_confirmed: bool = False
    current_semantics_verified: bool = False
    request_timeout_seconds: int = 20
    max_age_seconds: int = 3600
    model_selection: str = 'best_match'
    history_retention_days: int | None = 730
    poll_retention_days: int = 90
    raw_response_retention_days: int = 0
    plausibility: dict = field(default_factory=lambda: dict(DEFAULT_RANGES))

    @classmethod
    def from_mapping(cls, value):
        if not isinstance(value, dict) or set(value) - {f.name for f in fields(cls)}:
            raise ValueError('Invalid weather configuration fields')
        cfg = cls(**value)
        for flag in (cfg.enabled, cfg.eligible_use_confirmed, cfg.current_semantics_verified):
            if type(flag) is not bool:
                raise ValueError('Weather flags must be boolean')
        if cfg.provider != 'open_meteo' or cfg.model_selection != 'best_match':
            raise ValueError('Unsupported weather profile')
        for v, lo, hi in ((cfg.poll_interval_seconds, 900, 1800),
                          (cfg.request_timeout_seconds, 1, 120),
                          (cfg.max_age_seconds, 1, 86400),
                          (cfg.poll_retention_days, 1, 36500)):
            if type(v) is not int or not lo <= v <= hi:
                raise ValueError('Invalid weather duration')
        if cfg.history_retention_days is not None and (
            type(cfg.history_retention_days) is not int or cfg.history_retention_days < 1
        ):
            raise ValueError('Invalid weather retention')
        if type(cfg.raw_response_retention_days) is not int or cfg.raw_response_retention_days != 0:
            raise ValueError('Raw response retention is disabled in this release')
        for v in (cfg.location_id, cfg.location_epoch_id, cfg.device_id):
            if v is not None and (not isinstance(v, str) or not ID.fullmatch(v)):
                raise ValueError('Invalid opaque weather identity')
        try:
            ZoneInfo(cfg.display_timezone)
        except (ZoneInfoNotFoundError, TypeError, ValueError):
            raise ValueError('Invalid display timezone') from None
        if not isinstance(cfg.plausibility, dict) or set(cfg.plausibility) - DEFAULT_RANGES.keys():
            raise ValueError('Invalid plausibility metrics')
        for bounds in cfg.plausibility.values():
            if not isinstance(bounds, (list, tuple)) or len(bounds) != 2 or not all(map(number, bounds)) or bounds[0] >= bounds[1]:
                raise ValueError('Invalid outdoor plausibility range')
        if cfg.enabled and not all((cfg.location_id, cfg.location_epoch_id, cfg.device_id,
                                   cfg.coordinates_file, cfg.eligible_use_confirmed,
                                   cfg.current_semantics_verified)):
            raise ValueError('Weather activation requirements are incomplete')
        return cfg


def load_coordinates(cfg):
    """Explicit approval lives next to coordinates, never in public config or hashes."""
    try:
        path = Path(cfg.coordinates_file)
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
            raise ValueError()
        if info.st_size > 4096:
            raise ValueError()
        v = loads(path.read_text())
        if set(v) != {'latitude', 'longitude', 'approved_latitude', 'approved_longitude',
                      'approved_epoch_id', 'coordinate_sharing_approved'}:
            raise ValueError()
        lat, lon = v['latitude'], v['longitude']
        if not number(lat) or not number(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ValueError()
        if v['coordinate_sharing_approved'] is not True or v['approved_epoch_id'] != cfg.location_epoch_id:
            raise ValueError()
        if not number(v['approved_latitude']) or not number(v['approved_longitude']) or lat != v['approved_latitude'] or lon != v['approved_longitude']:
            raise ValueError()
        return lat, lon
    except (OSError, ValueError, TypeError, KeyError):
        raise ValueError('Protected coordinate approval is missing or invalid') from None


class UniqueLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader, node, deep=False):
    result = {}
    for key, value in node.value:
        k = loader.construct_object(key, deep=deep)
        if not isinstance(k, str) or k in result:
            raise ValueError('Duplicate or invalid configuration key')
        result[k] = loader.construct_object(value, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


@dataclass(frozen=True)
class Config:
    weather: WeatherConfig
    collector_id: str = 'odin-kari'
    database_path: str = '/var/lib/kari/kari.sqlite3'
    buffer_groups: int = 100


def load_config(path):
    try:
        root = yaml.load(Path(path).read_text(), Loader=UniqueLoader)
        if not isinstance(root, dict) or type(root.get('version')) is not int or root['version'] != 2:
            raise ValueError()
        if root.get('export', {}).get('enabled', False) is not False:
            raise ValueError('Live publishing is not available')
        collector = root.get('collector', {})
        identity = collector.get('id', 'odin-kari')
        if not isinstance(identity, str) or not ID.fullmatch(identity):
            raise ValueError()
        buffer = collector.get('buffer_groups_per_device', 100)
        if type(buffer) is not int or not 1 <= buffer <= 100:
            raise ValueError()
        weather = WeatherConfig.from_mapping(root.get('adapters', {}).get('weather', {}))
        return Config(weather, identity, collector.get('database_path', '/var/lib/kari/kari.sqlite3'), buffer)
    except (OSError, yaml.YAMLError, ValueError, TypeError, AttributeError):
        raise ValueError('Invalid configuration v2; inspect locally') from None
