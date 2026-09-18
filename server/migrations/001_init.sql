-- migrations/001_init.sql -- spec 6.2.
-- The migration runner wraps this file in its own transaction; do not add
-- BEGIN/COMMIT here.

CREATE TABLE products (
  barcode    TEXT PRIMARY KEY,
  name       TEXT,
  brand      TEXT,
  image_url  TEXT,
  source     TEXT NOT NULL,              -- openfoodfacts | manual | unknown
  fetched_at TEXT
) STRICT;

CREATE TABLE inventory (
  barcode    TEXT PRIMARY KEY,
  quantity   INTEGER NOT NULL CHECK (quantity >= 0),
  updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE scan_events (
  id             INTEGER PRIMARY KEY,
  source         TEXT NOT NULL,          -- scanner | manual
  nonce          BLOB UNIQUE,            -- NULL for manual edits
  barcode        TEXT NOT NULL,
  action         TEXT NOT NULL,          -- add | remove | adjust
  quantity_delta INTEGER NOT NULL,       -- change actually applied; 0 if rejected
  result         TEXT NOT NULL,
  received_at    TEXT NOT NULL
) STRICT;
