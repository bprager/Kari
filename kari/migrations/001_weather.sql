CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    schema_version TEXT NOT NULL,
    event_type TEXT NOT NULL,
    collected_at REAL NOT NULL,
    payload TEXT NOT NULL,
    payload_bytes INTEGER NOT NULL
);
CREATE TRIGGER IF NOT EXISTS immutable_events BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'immutable event'); END;
CREATE INDEX IF NOT EXISTS events_collection ON events(collected_at, seq);
CREATE TABLE IF NOT EXISTS samples (
    sample_id TEXT PRIMARY KEY,
    sample_key TEXT NOT NULL UNIQUE,
    latest_revision INTEGER NOT NULL,
    latest_event_id TEXT NOT NULL,
    evidence TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS weather_details (
    event_id TEXT PRIMARY KEY REFERENCES events(event_id) ON DELETE CASCADE,
    sample_id TEXT NOT NULL REFERENCES samples(sample_id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    location_epoch_id TEXT NOT NULL,
    metric TEXT NOT NULL,
    valid_at REAL NOT NULL,
    UNIQUE(sample_id, revision)
);
CREATE INDEX IF NOT EXISTS weather_location_metric_time ON weather_details(location_epoch_id, metric, valid_at);
CREATE INDEX IF NOT EXISTS weather_valid_time ON weather_details(valid_at);
CREATE TABLE IF NOT EXISTS polls (
    collection_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    scheduled_at REAL NOT NULL,
    attempted_at REAL NOT NULL,
    received_at REAL,
    outcome TEXT NOT NULL,
    transport_success INTEGER NOT NULL DEFAULT 0,
    latest_valid_at REAL,
    missing TEXT NOT NULL DEFAULT '[]',
    issues TEXT NOT NULL DEFAULT '[]',
    attempted INTEGER NOT NULL DEFAULT 1,
    scheduled_count INTEGER NOT NULL DEFAULT 1,
    gaps INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS polls_attempted ON polls(attempted_at);
CREATE TABLE IF NOT EXISTS outbox (
    event_id TEXT PRIMARY KEY REFERENCES events(event_id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK(status IN ('pending','acked','rejected')),
    acknowledgement TEXT
);
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS epochs (
    epoch_id TEXT PRIMARY KEY,
    location_id TEXT NOT NULL,
    latitude REAL,
    longitude REAL
);
PRAGMA user_version = 1;
