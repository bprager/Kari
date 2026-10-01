"""Descriptive sample statistics; no gap filling or time-weighted inference."""
from datetime import datetime, timezone
from kari.store import seconds
from kari.weather import VARIABLES, stamp


def summarize(store, end, epoch):
    at = lambda value: stamp(datetime.fromtimestamp(value, timezone.utc))
    groups = {row[1]: [] for row in VARIABLES}
    for event in store.iter_query(start=at(end-86400), as_of=at(end), epoch=epoch):
        data = event['data']
        valid = seconds(data['valid_at'])
        if valid > end or data['quality'] != 'valid' or data['valid_time_freshness'] != 'current':
            continue
        groups[data['metric']].append((valid, data['value']))
    result = {}
    for metric, points in groups.items():
        if not points:
            continue
        values = [value for _, value in points]
        recent = [value for valid, value in points if valid >= end-3600]
        result[metric] = dict(latest=points[-1][1], latest_valid_timestamp_seconds=points[-1][0],
                              average_24h=sum(values)/len(values), minimum_24h=min(values),
                              maximum_24h=max(values), count_24h=len(values))
        if recent:
            result[metric]['average_1h'] = sum(recent)/len(recent)
    return result
