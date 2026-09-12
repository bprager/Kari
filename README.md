# Kári

Environmental collection for Napoleon, running independently on Odin.

Planned adapters:
- ThermoPro TP350S: passive BLE temperature, humidity, and RSSI.
- Levoit Core 300S: read-only PM2.5 and purifier state through VeSync.

The repository currently defines requirements and a proposed ingestion contract. Collectors, deployment, notifications, and a live Napoleon receiver are not implemented by this change.

- [PRD](PRD.md)
- [Ingestion semantics and migration](docs/ingestion.md)
- [Event JSON Schema](schemas/kari-event-v1.schema.json)
- [Acknowledgement JSON Schema](schemas/kari-ack-v1.schema.json)
- [Synthetic example events](examples/events.jsonl)

## Validate the contract

Use Python 3.11+ in a virtual environment:

```sh
python -m pip install -r requirements-contract.txt
python scripts/validate_contract.py examples/events.jsonl
python -m unittest discover -s tests -v
```

Validation is offline and sends no device commands or notifications. It checks schema shape plus event-level semantics; receiver authentication, delivery idempotency, and real sensor accuracy require later integration tests.
