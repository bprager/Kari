"""Offline contract validation. No collector, networking, or device commands."""
from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
CHECKER = FormatChecker()


@CHECKER.checks("date-time", raises=(ValueError, TypeError))
def utc_timestamp(value):
    if not isinstance(value, str):
        return True  # Type validation is the schema's responsibility.
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", value):
        return False
    datetime.fromisoformat(value.replace("Z", "+00:00"))
    return True


def reject_constant(value):
    raise ValueError("Nonfinite JSON number")


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def loads(text):
    return json.loads(text, parse_constant=reject_constant, object_pairs_hook=unique_keys)


def finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Nonfinite number")
    if isinstance(value, dict):
        for item in value.values():
            finite(item)
    elif isinstance(value, list):
        for item in value:
            finite(item)


def validator(filename):
    schema = loads((ROOT / "schemas" / filename).read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=CHECKER)


EVENT = validator("kari-event-v1.schema.json")
EVENT_WEATHER = validator("kari-event-v1.1.schema.json")
EVENT_VERSIONS = {"1.0.0": EVENT, "1.1.0": EVENT_WEATHER}
ACK = validator("kari-ack-v1.schema.json")


def moment(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def validate_event(event, battery_capabilities=None):
    finite(event)
    if not isinstance(event, dict) or event.get("schema_version") not in EVENT_VERSIONS:
        raise ValueError("Unsupported exact event version")
    EVENT_VERSIONS[event["schema_version"]].validate(event)
    observed = event["observed_at"]
    collected = moment(event["collected_at"])
    if moment(event["persisted_at"]) < collected:
        raise ValueError("Persistence precedes collection")
    if observed is not None:
        age = (collected - moment(observed)).total_seconds()
        if age < 0:
            raise ValueError("Observation follows collection")
        expected = "current" if age <= event["max_age_seconds"] else "stale"
        if event["freshness_at_collection"] != expected:
            raise ValueError("Freshness contradicts evidence age")
        if event["timestamp_basis"] == "collector_receive" and age != 0:
            raise ValueError("Reception timestamp must equal collection time")
    adapter = event["source"]["adapter"]
    if event["event_type"] == "observation":
        metric = event["data"]["metric"]
        allowed = {"thermopro_ble": {"temperature", "relative_humidity", "rssi", "battery"},
                   "vesync": {"pm2_5"}}
        if metric not in allowed.get(adapter, set()):
            raise ValueError("Metric unsupported by adapter")
        if metric == "battery" and (battery_capabilities or {}).get(event["device_id"]) != "verified":
            raise ValueError("Battery capability must be explicitly verified")
        if adapter == "thermopro_ble" and event["timestamp_basis"] != "collector_receive":
            raise ValueError("BLE observations require reception time")
    elif event["event_type"] == "weather_context":
        data = event["data"]
        valid = moment(data["valid_at"])
        age = (collected - valid).total_seconds()
        if age < 0:
            raise ValueError("Modeled valid time follows collection")
        expected = "current" if age <= event["max_age_seconds"] else "stale"
        if data["valid_time_freshness"] != expected:
            raise ValueError("Weather freshness contradicts valid time")
        support = data["temporal_support"]
        start, end = support["interval_start"], support["interval_end"]
        if support["statistic"] == "instantaneous":
            if start is not None or end is not None:
                raise ValueError("Instantaneous values cannot have interval boundaries")
        elif start is None or end is None or moment(end) != valid or (
            moment(end) - moment(start)
        ).total_seconds() != support["nominal_step_seconds"]:
            raise ValueError("Invalid backward-looking mean interval")
        if (data["revision"] == 1) != (data["supersedes_event_id"] is None):
            raise ValueError("Revision must identify its predecessor")
        if data["supersedes_event_id"] == event["event_id"]:
            raise ValueError("Event cannot supersede itself")
        if (data["quality"] == "suspect") != bool(data["quality_flags"]):
            raise ValueError("Quality flags must explain suspect evidence")
    elif event["event_type"] == "health_event":
        data = event["data"]
        started = moment(data["started_at"])
        if started > collected:
            raise ValueError("Incident starts after collection")
        if data["resolved_at"] is not None:
            resolved = moment(data["resolved_at"])
            if not started <= resolved <= collected:
                raise ValueError("Invalid incident resolution time")
    return event


def validate_ack(ack, expected_event_id):
    finite(ack)
    ACK.validate(ack)
    if ack["event_id"] != expected_event_id:
        raise ValueError("Acknowledgement does not match request")
    return ack


def validate_lines(lines):
    seen = {}
    count = 0
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            if len(line.rstrip("\r\n").encode("utf-8")) > 16384:
                raise ValueError("Event exceeds 16 KiB")
            event = validate_event(loads(line))
            event_id = event["event_id"]
            if event_id in seen and seen[event_id] != event:
                raise ValueError("Conflicting payload for event ID")
            seen[event_id] = event
        except Exception as exc:
            # Keep input values and possible secrets out of the CLI error.
            raise ValueError(f"Invalid event at line {number}: {type(exc).__name__}") from exc
        count += 1
    if not count:
        raise ValueError("No events")
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("jsonl", type=Path)
    args = parser.parse_args()
    try:
        with args.jsonl.open(encoding="utf-8") as stream:
            count = validate_lines(stream)
    except (ValueError, OSError):
        parser.exit(1, "Contract validation failed; inspect the input locally.\n")
    print(f"Validated {count} events")


if __name__ == "__main__":
    main()
