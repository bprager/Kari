"""Pure Open-Meteo current normalization. No raw response fields are exported."""
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

from kari.config import DEFAULT_RANGES, number
from scripts.validate_contract import moment, utc_timestamp

# provider variable, metric, wire unit, provider unit, height in metres
VARIABLES = (
    ('temperature_2m', 'temperature', 'Cel', '°C', 2),
    ('relative_humidity_2m', 'relative_humidity', '%', '%', 2),
    ('dew_point_2m', 'dew_point', 'Cel', '°C', 2),
    ('wind_speed_10m', 'wind_speed', 'm/s', 'm/s', 10),
    ('shortwave_radiation', 'shortwave_radiation', 'W/m2', 'W/m²', None),
)


def stamp(value):
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def utcnow():
    return stamp(datetime.now(timezone.utc))


@dataclass
class Normalized:
    metrics: list
    missing: list
    issues: list


def normalize(body, cfg, collected_at):
    if not isinstance(body, dict) or not isinstance(body.get('current'), dict) or not isinstance(body.get('current_units'), dict):
        raise ValueError('Malformed current response')
    current, units = body['current'], body['current_units']
    if type(body.get('utc_offset_seconds')) is not int or body['utc_offset_seconds'] != 0 or units.get('time') != 'unixtime':
        raise ValueError('Invalid provider time metadata')
    value_time = current.get('time')
    if type(value_time) is not int or not utc_timestamp(collected_at):
        raise ValueError('Invalid provider time')
    try:
        valid = datetime.fromtimestamp(value_time, timezone.utc)
        age = (moment(collected_at) - valid).total_seconds()
    except (ValueError, OverflowError, OSError):
        raise ValueError('Invalid provider time') from None
    if age < 0:
        raise ValueError('clock_invalid')
    step = current.get('interval')
    if type(step) is not int or not 0 < step <= 86400 or units.get('interval') != 'seconds':
        # All metrics require an honest positive nominal step, not just radiation.
        return Normalized([], [m[1] for m in VARIABLES], ['invalid_interval'])
    metrics, missing, issues = [], [], []
    for variable, metric, unit, provider_unit, height in VARIABLES:
        v = current.get(variable)
        if not number(v) or units.get(variable) != provider_unit or (
            metric == 'relative_humidity' and not 0 <= v <= 100
        ) or (metric in ('wind_speed', 'shortwave_radiation') and v < 0) or (
            metric in ('temperature', 'dew_point') and v < -273.15
        ):
            missing.append(metric)
            issues.append('invalid_metric')
            continue
        lo, hi = cfg.plausibility.get(metric, DEFAULT_RANGES[metric])
        suspect = not lo <= v <= hi
        is_mean = metric == 'shortwave_radiation'
        metrics.append(dict(
            data_kind='modeled_current', location_epoch_id=cfg.location_epoch_id,
            metric=metric, value=v, unit=unit, quality='suspect' if suspect else 'valid',
            quality_flags=['outside_plausible_range'] if suspect else [],
            valid_at=stamp(valid), valid_time_freshness='current' if age <= cfg.max_age_seconds else 'stale',
            temporal_support=dict(statistic='mean' if is_mean else 'instantaneous',
                                  interval_start=stamp(valid - timedelta(seconds=step)) if is_mean else None,
                                  interval_end=stamp(valid) if is_mean else None,
                                  nominal_step_seconds=step),
            provenance=dict(provider='open_meteo', product='forecast_current',
                            model_selection=cfg.model_selection, model_id=None, model_run_id=None,
                            source_variable=variable, height_m=height)))
    return Normalized(metrics, missing, sorted(set(issues)))
