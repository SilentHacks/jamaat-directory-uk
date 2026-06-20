CREATE TABLE IF NOT EXISTS mosque (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    aliases     TEXT NOT NULL DEFAULT '[]',  -- JSON array
    address     TEXT,
    city        TEXT,
    postcode    TEXT,
    country     TEXT NOT NULL DEFAULT 'GB',
    lat         REAL NOT NULL,
    lng         REAL NOT NULL,
    website_url TEXT,
    status      TEXT NOT NULL DEFAULT 'active',  -- active | inactive | needs_review
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_mosque_city ON mosque(city);
CREATE INDEX IF NOT EXISTS idx_mosque_latlng ON mosque(lat, lng);

CREATE TABLE IF NOT EXISTS source (
    id               TEXT PRIMARY KEY,
    mosque_id        TEXT NOT NULL REFERENCES mosque(id),
    url              TEXT,
    platform         TEXT,
    shape            TEXT,
    config           TEXT,                      -- JSON
    requires_js      INTEGER NOT NULL DEFAULT 0,
    triage_status    TEXT NOT NULL DEFAULT 'candidate',
    confidence       REAL,
    review_reason    TEXT,
    authored_by      TEXT,
    authored_at      TEXT,
    source_html_hash TEXT,
    last_fetched_at  TEXT,
    last_status      TEXT,
    last_error       TEXT
);

CREATE INDEX IF NOT EXISTS idx_source_mosque ON source(mosque_id);
CREATE INDEX IF NOT EXISTS idx_source_triage ON source(triage_status);

CREATE TABLE IF NOT EXISTS occurrence (
    mosque_id    TEXT NOT NULL REFERENCES mosque(id),
    date         TEXT NOT NULL,                 -- ISO date
    prayer       TEXT NOT NULL,                 -- fajr|dhuhr|asr|maghrib|isha|jumuah
    session_idx  INTEGER NOT NULL DEFAULT 0,    -- 0 daily; 1..N jumuah sessions
    jamaah_time  TEXT NOT NULL,                 -- HH:MM
    begin_time   TEXT,                          -- HH:MM, nullable
    label        TEXT,                          -- jumuah session label, nullable
    source_id    TEXT REFERENCES source(id),
    extracted_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (mosque_id, date, prayer, session_idx)
);

CREATE INDEX IF NOT EXISTS idx_occurrence_date_prayer ON occurrence(date, prayer);

CREATE TABLE IF NOT EXISTS extractor_run (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id   TEXT REFERENCES source(id),
    started_at  TEXT NOT NULL DEFAULT (datetime('now')),
    ok          INTEGER NOT NULL DEFAULT 0,
    rows_written INTEGER NOT NULL DEFAULT 0,
    error       TEXT
);
