import copy
import importlib.util
import json
from pathlib import Path
import unittest

from jsonschema import ValidationError

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("contract", ROOT / "scripts/validate_contract.py")
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)
EXAMPLES = [contract.loads(line) for line in (ROOT / "examples/events.jsonl").read_text().splitlines()]


class ContractTests(unittest.TestCase):
    def test_examples(self):
        self.assertEqual(contract.validate_lines(json.dumps(e) for e in EXAMPLES), 8)

    def test_rejections(self):
        changes = [
            (0, ("schema_version",), "2.0.0"),
            (0, ("event_id",), "not-a-uuid"),
            (0, ("collected_at",), "2026-02-30T05:00:00Z"),
            (0, ("collected_at",), "2026-09-12T05:00:00"),
            (0, ("collected_at",), "2026-09-12T05:00:00+00:00"),
            (0, ("persisted_at",), "2026-09-12T04:59:00Z"),
            (0, ("observed_at",), "2026-09-12T05:01:00Z"),
            (0, ("observed_at",), None),
            (0, ("data", "metric"), "co2"),
            (0, ("data", "value"), True),
            (0, ("data", "value"), -300),
            (0, ("data", "value"), float("inf")),
            (0, ("device_id",), None),
            (1, ("data", "value"), 101),
            (2, ("data", "unit"), "%"),
            (3, ("data", "unit"), "ppm"),
            (3, ("data", "value"), -1),
            (3, ("freshness_at_collection",), "current"),
            (3, ("timestamp_basis",), "collector_receive"),
            (3, ("data", "metric"), "temperature"),
            (3, ("data",), {}),
            (4, ("data",), {}),
            (4, ("data", "filter_life_percent"), 142),
            (4, ("data", "filter_life_basis"), "measured"),
            (5, ("freshness_at_collection",), "current"),
            (6, ("data", "scope"), "device"),
            (6, ("data", "started_at"), "2026-09-12T05:01:00Z"),
            (7, ("data", "resolved_at"), None),
            (7, ("data", "resolved_at"), "2026-09-12T04:00:00Z"),
            (0, ("account_token",), "forbidden-extra-field"),
        ]
        for index, path, value in changes:
            with self.subTest(index=index, path=path, value=value):
                event = copy.deepcopy(EXAMPLES[index])
                target = event
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                with self.assertRaises((ValueError, ValidationError)):
                    contract.validate_event(event)

    def test_cloud_with_verified_time_and_staleness_boundary(self):
        event = copy.deepcopy(EXAMPLES[3])
        event.update(observed_at="2026-09-12T04:50:00Z",
                     timestamp_basis="device", freshness_at_collection="current")
        contract.validate_event(event)
        event["observed_at"] = "2026-09-12T04:49:59Z"
        with self.assertRaises(ValueError):
            contract.validate_event(event)
        event["freshness_at_collection"] = "stale"
        contract.validate_event(event)

    def test_optional_state_and_zero_particle_reading(self):
        event = copy.deepcopy(EXAMPLES[4])
        event["data"] = {"power": "off"}
        contract.validate_event(event)
        event = copy.deepcopy(EXAMPLES[3])
        event["data"]["value"] = 0
        contract.validate_event(event)

    def test_battery_needs_verified_device_capability(self):
        event = copy.deepcopy(EXAMPLES[0])
        event["data"] = {"metric": "battery", "value": 10, "unit": "%", "quality": "valid"}
        with self.assertRaises(ValueError):
            contract.validate_event(event)
        with self.assertRaises(ValueError):
            contract.validate_event(event, {event["device_id"]: "unverified"})
        contract.validate_event(event, {event["device_id"]: "verified"})

    def test_duplicate_delivery_and_conflict(self):
        original = json.dumps(EXAMPLES[0])
        reordered = json.dumps(EXAMPLES[0], sort_keys=True)
        self.assertEqual(contract.validate_lines([original, reordered]), 2)
        changed = copy.deepcopy(EXAMPLES[0])
        changed["data"]["value"] += 1
        with self.assertRaises(ValueError):
            contract.validate_lines([original, json.dumps(changed)])

    def test_distinct_acquisitions_with_equal_values(self):
        event = copy.deepcopy(EXAMPLES[3])
        event["event_id"] = "00000000-0000-4000-8000-000000000099"
        event["collection_id"] = "10000000-0000-4000-8000-000000000099"
        self.assertEqual(contract.validate_lines([json.dumps(EXAMPLES[3]), json.dumps(event)]), 2)

    def test_strict_json(self):
        for text in ['{"a": 1, "a": 2}', '{"value": NaN}', '{"value": Infinity}']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                contract.loads(text)
        event = copy.deepcopy(EXAMPLES[3])
        line = json.dumps(event).replace('"value": 8', '"value": 1e9999')
        with self.assertRaises(ValueError):
            contract.validate_lines([line])
        with self.assertRaises(ValueError):
            contract.validate_lines([" " * 20000 + json.dumps(EXAMPLES[0])])

    def test_acknowledgements(self):
        expected_id = EXAMPLES[0]["event_id"]
        ack = {"schema_version": "1.0.0", "event_id": expected_id,
               "status": "accepted", "stored_at": "2026-09-12T05:01:00Z"}
        contract.validate_ack(ack, expected_id)
        ack["status"] = "duplicate"
        contract.validate_ack(ack, expected_id)
        with self.assertRaises(ValueError):
            contract.validate_ack(ack, "00000000-0000-4000-8000-000000000099")
        ack["status"] = "queued"
        with self.assertRaises(ValidationError):
            contract.validate_ack(ack, expected_id)


if __name__ == "__main__":
    unittest.main()
