# Kári

Environmental collection for Napoleon, running independently on Odin.

Host policy: Bluetooth/BLE services run on **Mimir only**, never Odin. The weather
pilot runs on Odin and uses no Bluetooth. See the [pilot design and deployment
record](docs/weather-pilot-design.md).

Adapters:
- Planned ThermoPro TP350S: passive BLE temperature, humidity, and RSSI.
- Planned Levoit Core 300S: read-only PM2.5 and purifier state through VeSync.

The optional Open-Meteo fixed HOME weather slice is implemented with local history, revision-aware queries, health, and offline export. It is disabled by default and has not been deployed. BLE/VeSync collectors, notifications, and live receiver publishing remain unimplemented. Fjölsviðr (formerly Napoleon) owns downstream interpretation; existing environment-variable names are preserved.

- [Fixed HOME weather ADR](docs/ADR-kari-local-weather-context.md)
- [Weather design and implementation plan](docs/weather-design.md)
- [Weather operations, activation gates, and pilot checklist](docs/weather-operations.md)
- [Disabled configuration v2 example](examples/config-v2.yaml)
- [Weather event schema 1.1.0](schemas/kari-event-v1.1.schema.json)
- [PRD](PRD.md)
- [Ingestion semantics and migration](docs/ingestion.md)
- [Event JSON Schema](schemas/kari-event-v1.schema.json)
- [Acknowledgement JSON Schema](schemas/kari-ack-v1.schema.json)
- [Synthetic example events](examples/events.jsonl)

## Validate the contract

Use Python 3.11+ in a virtual environment:

```sh
python -m pip install -r requirements.txt
python scripts/validate_contract.py examples/events.jsonl
python scripts/validate_contract.py examples/weather-events.jsonl
python -m kari --config examples/config-v2.yaml validate-config
python -m unittest discover -s tests -v
```

Validation is offline and sends no device commands or notifications. It checks schema shape plus event-level semantics; receiver authentication, delivery idempotency, and real sensor accuracy require later integration tests.
