# Pantry Inventory System — Specification v2.0 (Personal)

**Status:** authoritative. Supersedes v1.0 and every earlier spec. Where a companion document disagrees with this spec, this spec wins.

Companion documents that remain useful: `phase-0-uart-waveshare-setup.md` (hardware bring-up) and `c-crash-course-scanner-client.md` (C background). File, function, and endpoint names in this spec replace any older names used there.

---

## 1. Scope

A pantry inventory for one household. A barcode scanner on a Raspberry Pi reports items added to or removed from the pantry. A server on a mini PC keeps the counts and shows them on a web page reachable at home and, through Tailscale, from anywhere.

This spec covers the whole system: scanner client, wire protocol, server, storage, access control, deployment, and backup. It is sized for one household, one scanner, and one person maintaining it. It is not a product and makes no allowances for becoming one.

---

## 2. Architecture

```
┌──────────────────┐         ┌───────────────────────────────┐
│  Raspberry Pi    │         │  Mini PC (always on)          │
│                  │         │                               │
│  Waveshare       │  HTTP   │  FastAPI ──── SQLite          │
│  module ──UART── │  POST   │     │                         │
│  add/remove      │ ──────► │     ├── static web page       │
│  switch ──GPIO── │  LAN    │     └── JSON API              │
│  C client        │         │                               │
│  buzzer ──GPIO── │         │  Tailscale · restic           │
└──────────────────┘         └───────────────────────────────┘
```

Three parts, each with one job:

1. **Scanner** — reports a barcode and an add/remove flag. Holds no inventory, no product names, and nothing that survives a reboot.
2. **API** — the only thing the scanner and the web page talk to.
3. **Server** — all logic and all stored data.

The scanner never touches the database. Keeping all logic on the server keeps the scanner program small and means a counting bug gets fixed in one place.

---

## 3. Hardware

| Item | Specification |
|---|---|
| Scan engine | Waveshare barcode module, configured for **UART output**, 9600 8N1, CR or LF terminator |
| Host | Raspberry Pi, UART on `/dev/serial0` |
| Mode control | SPDT switch, one pole to a GPIO input with pull-up. Closed = remove, open = add |
| Feedback | Piezo buzzer and LED on GPIO outputs, both optional |
| Server | Always-on Linux mini PC, LAN, DHCP reservation, Tailscale member |

**Pi UART preparation, required:**

- Serial login shell disabled, serial hardware enabled (`raspi-config`)
- On Pi 3 and newer: `dtoverlay=disable-bt` in `/boot/firmware/config.txt`, to move the PL011 UART to the GPIO header. The mini UART's baud rate follows the core clock and produces intermittent corruption under load.
- Address the port as `/dev/serial0`, never `ttyS0` or `ttyAMA0`

**Module configuration**, applied by scanning setup barcodes from the vendor manual: UART output mode (modules ship in USB-keyboard mode), 9600 8N1, an end-mark suffix, and the desired trigger mode. Save to flash.

**Electrical:** Pi GPIO is 3.3V and not 5V tolerant. Verify the module's TX idle level before connecting, or fit a divider (1kΩ series, 2kΩ to ground) on module-TX → Pi-RX.

---

## 4. Wire protocol

The contract between the scanner and the server.

### Endpoint

```
POST /api/scans
Authorization: Bearer <API_TOKEN>
Content-Type: application/json
```

### Request

```json
{
  "scans": [
    {"nonce": "3f8a1c9d2b4e6f70", "barcode": "041196891010", "action": "add"}
  ]
}
```

| Field | Type | Constraint |
|---|---|---|
| `nonce` | string | Exactly 16 lowercase hex chars (8 random bytes) |
| `barcode` | string | 1–32 chars, `[A-Za-z0-9-]` only |
| `action` | string | `add` or `remove` |

Batch size 1–100. The scanner never sends more than 32, the size of its buffer.

### Response

`200 OK`, one result per request element, in request order:

```json
{
  "results": [
    {"nonce": "3f8a1c9d2b4e6f70", "result": "applied", "quantity": 3}
  ]
}
```

| `result` | Meaning |
|---|---|
| `applied` | Quantity changed |
| `rejected_not_in_stock` | Remove attempted at quantity 0; nothing changed |
| `duplicate` | This nonce was already processed; nothing changed |
| `invalid` | Element failed validation; nothing changed |

`quantity` is the item's quantity after processing, omitted for `invalid`. `nonce` echoes the element's nonce as sent, or `null` if it was missing.

### Client-side rules

1. **The nonce is generated when the barcode is scanned and never regenerated.** It stays the same across every retry of that scan. Regenerating on retry defeats deduplication and silently multiplies inventory.
2. **`duplicate` is a success.** The server already has the scan; an earlier acknowledgement was lost. Free the slot.
3. **A result of any kind frees the slot.** Only the absence of a result means retry. (`invalid` will never succeed, so retrying it is pointless.)
4. **HTTP 401 is permanent.** Log, signal on the buzzer, and stop sending until the process is restarted with a corrected token.

### Errors

```json
{"error": {"code": "invalid_token", "message": "Missing or incorrect token"}}
```

Codes: `invalid_token`, `invalid_request`, `not_found`. Clients switch on `code`, never on message text.

---

## 5. Scanner client

Written in C. Writing a serial, buffering, and retry client in C is part of the point of the project.

### 5.1 Structure

Single-threaded. No threads, no mutexes, no shared state. A `poll()` loop with a computed timeout.

```
init: load .env, open serial, configure termios, curl_global_init, zero buffer

loop:
  timeout = ms until next retry due, else 60000
  poll(serial_fd, timeout)
  if readable:   read, frame, on complete barcode → enqueue, attempt now
  if hangup:     close, reopen with backoff
  if retry due:  build batch, POST, release acknowledged slots
```

### 5.2 Serial input

Open `O_RDWR | O_NOCTTY | O_NONBLOCK`. Configure via `tcgetattr` → modify → `tcsetattr`:

- 9600 baud, CS8, no parity, one stop bit, no flow control, `CREAD | CLOCAL`
- Clear `ICANON`, `ECHO`, `ECHOE`, `ECHONL`, `ISIG`
- Clear `IXON`, `IXOFF`, `IXANY`
- Clear `IGNBRK`, `BRKINT`, `PARMRK`, `ISTRIP`, `INLCR`, `IGNCR`, `ICRNL`
- Clear `OPOST`, `ONLCR`
- `VMIN = 0`, `VTIME = 0`

`ICANON`, `ICRNL`, and `IXON` are on by default and all three are wrong here. `ICRNL` silently rewrites CR to LF; `IXON` removes `0x11`/`0x13` from the data stream.

`read()` returns arbitrary chunk sizes. Treat `EINTR` and `EAGAIN` as retry, not error.

### 5.3 Framing

Fixed 64-byte buffer. Accept both CR and LF as terminators and swallow empty frames, so CRLF, CR-only, and LF-only module settings all work. Discard non-printable bytes. On overlong input, discard and resync at the next terminator.

**Stale-frame timeout:** discard buffered bytes older than 200ms with no terminator. Otherwise a partial read gets glued onto the front of the next barcode.

### 5.4 Mode switch

Read the GPIO **at the moment a barcode completes**, not on an edge and not at send time. No interrupt handler and no debounce; a level read at scan time is stable.

Use `libgpiod`, not the deprecated sysfs interface.

The switch position is the entire mode mechanism. There is no server-side mode, no mode barcodes, and no timers.

### 5.5 In-flight buffer

RAM only. Fixed array of 32 entries.

```c
typedef struct {
    uint8_t  nonce[8];
    char     barcode[33];   // 32 chars + NUL
    uint8_t  action;        // 0 add, 1 remove
    bool     occupied;
    uint64_t queued_ms;
} scan_rec_t;
```

**Overflow: drop oldest.** During an outage the most recent scans matter more. Log at ERROR and signal distinctly on the buzzer, since a full buffer means lost data and the person scanning should know.

The buffer covers network blips and server restarts. It does not survive a power cut, which is acceptable for a pantry.

### 5.6 Retry timing

```c
static const uint32_t backoff_ms[] =
    {0, 1000, 2000, 5000, 15000, 30000, 60000, 120000};
```

The index advances on failure and resets to 0 on success. A new scan sets the next attempt to *now* but **does not reset the index**; otherwise scanning repeatedly during a real outage produces a flood of requests.

### 5.7 HTTP

libcurl, one easy handle reused for the life of the process so the TCP connection stays open between requests.

Required options: `CURLOPT_TIMEOUT` (10s), `CURLOPT_CONNECTTIMEOUT` (5s), `CURLOPT_NOSIGNAL`, `CURLOPT_WRITEFUNCTION`. Without the first two, a dead server hangs the loop. Without the third, `SIGPIPE` can kill the process. Without the fourth, the response body is printed to stdout.

Check `CURLcode` and HTTP status separately; they are different failures.

Build request JSON with `snprintf` (the shape is fixed and inputs are already validated). Parse responses with cJSON, vendored as two files in the repository so there is nothing to install. Null-check every accessor: a malformed response is a failed request, not a crash.

### 5.8 Feedback

| Signal | Meaning |
|---|---|
| One short beep | Add applied |
| Two short beeps | Remove applied |
| Long low tone | Rejected, not in stock |
| Repeating pattern | Queued, server unreachable |
| Rapid triple | Buffer full, data lost |
| Three long tones | Token rejected (401); scanner has stopped sending |

Controlled by `FEEDBACK_ENABLED`, so the client also runs on a laptop with no buzzer.

### 5.9 Code organization

All hardware and OS-specific code lives in `hw_linux.c`, behind `hw.h`. The logic modules (framing, queue, backoff, JSON) never read the clock or touch hardware themselves. They take the current time as a function parameter. That lets them be unit-tested on a laptop with no Pi attached, by passing in whatever times a test needs.

```c
// hw.h
typedef enum {
    SIG_ADD, SIG_REMOVE, SIG_REJECTED, SIG_QUEUED, SIG_OVERFLOW, SIG_AUTH_FAILED
} signal_t;

int      hw_serial_open(const char *path);            // fd, or -1
int      hw_serial_read(int fd, uint8_t *buf, size_t len);
void     hw_serial_close(int fd);
bool     hw_switch_is_remove(void);
void     hw_signal(signal_t s);                       // buzzer + LED; no-op if disabled
int      hw_http_post(const char *url, const char *token, const char *body,
                      char *resp, size_t resp_len, long *status);
uint64_t hw_now_ms(void);
void     hw_random(uint8_t *buf, size_t len);         // getrandom()
```

`hw_now_ms` uses `CLOCK_MONOTONIC`, never `CLOCK_REALTIME`. The Pi has no battery-backed clock, and its wall clock jumps when NTP syncs.

`main.c` owns the `poll()` loop and wires the modules together.

Use fixed-size buffers throughout. Nothing in this program needs `malloc`.

### 5.10 Configuration

`.env` read at startup:

```
SERVER_URL=http://miniserver.local:8080
API_TOKEN=<token>
SERIAL_PORT=/dev/serial0
SWITCH_GPIO=17
BUZZER_GPIO=27
LED_GPIO=22
FEEDBACK_ENABLED=1
```

`SERVER_URL` holds a LAN hostname, not an IP and not a Tailscale name. Don't cache the resolved address yourself; libcurl's DNS cache expires after 60 seconds by default, so an address change is picked up on its own.

---

## 6. Server

### 6.1 Stack

Python 3.12, FastAPI, Uvicorn, SQLite. Dependencies: `fastapi`, `uvicorn[standard]`, `httpx`. No ORM, no template engine.

**Handlers are `def`, not `async def`.** A blocking call inside an async handler stalls the whole server. FastAPI runs plain `def` handlers in a threadpool where blocking is safe. Use `async def` only where the body genuinely awaits.

**One uvicorn worker.** Multiple processes writing one SQLite file produce intermittent `database is locked` errors.

**The server refuses to start if `API_TOKEN` is missing or shorter than 32 characters,** so a misconfigured deploy can't come up with an open API.

### 6.2 Schema

Set once at startup, before migrations: `PRAGMA journal_mode = WAL` (persists in the file). Set on every connection: `PRAGMA busy_timeout = 5000`.

```sql
-- migrations/001_init.sql

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
```

Notes:

- SQLite allows any number of NULLs in a `UNIQUE` column, so manual edits (NULL nonce) never collide.
- `quantity_delta` records the change that actually happened, so an item's quantity always equals the sum of its deltas.
- **No `scanned_at` column.** The scanner has no clock and no persistence; `received_at` is the only honest timestamp.
- `inventory` is derived; `scan_events` is the record of truth. The `replay-events` command rebuilds `inventory` from `scan_events`: in one transaction, delete all inventory rows, then insert `SUM(quantity_delta)` and `MAX(received_at)` per barcode over events with `result = 'applied'`.

### 6.3 Database access

One connection per request, via a FastAPI dependency. `sqlite3.connect(path, isolation_level=None)` so Python's automatic transaction handling is off and `BEGIN`/`COMMIT` are explicit. `row_factory = sqlite3.Row`. All SQL lives in `app/db.py`, always with `?` parameters.

**Migrations:** numbered `.sql` files in `migrations/`, tracked with `PRAGMA user_version`. At startup, apply any file numbered above the current `user_version`, in order, each in its own transaction, then update `user_version`.

Timestamps are ISO-8601 UTC text ending in `Z`.

### 6.4 Scan handling

The request body is accepted as a list of 1–100 raw objects, and **each element is validated individually** in the handler. (Validating the whole body with one strict Pydantic model would reject the entire batch for a single bad element, which breaks acceptance criterion 2.)

For each element, in order, in its own transaction so one element can never undo another:

1. Validate against the table in section 4. On failure, result is `invalid`; no database write.
2. `BEGIN IMMEDIATE`. This takes the write lock up front; a deferred transaction can fail with `SQLITE_BUSY` after work is already done.
3. Read the current quantity (0 if no row).
4. Decide: `add` → delta +1, `applied`. `remove` with quantity > 0 → delta −1, `applied`. `remove` at 0 → delta 0, `rejected_not_in_stock`.
5. Insert into `scan_events`. **A unique-constraint violation on `nonce` is the duplicate check**: catch `sqlite3.IntegrityError`, `ROLLBACK`, result `duplicate`. Never check for the nonce with a SELECT first.
6. If delta ≠ 0, insert or update the `inventory` row.
7. `COMMIT`.

After the loop, schedule a product lookup (6.7) for any barcode that needs one. Never make the scanner wait for it.

Scan handling lives in its own file, `app/scans.py`.

### 6.5 Access control

One shared secret, `API_TOKEN`. It is set in the server's `.env` and the scanner's `.env`, and typed once into the web page. Every `/api/` route requires `Authorization: Bearer <API_TOKEN>`, checked by a single FastAPI dependency using `hmac.compare_digest`. The static page at `/` is served without a token; it contains no data until it calls the API.

Generate a token with `python -c "import secrets; print(secrets.token_hex(32))"`. To change it, update both `.env` files, restart both services, and re-enter it on the web page.

One credential covers everything. It keeps out other devices on the home network and anyone who reaches the port by accident. It does not tell household members apart.

### 6.6 API

All routes require the token.

```
POST   /api/scans                        scanner batch (section 4)
GET    /api/inventory                    all items, joined with product names
PATCH  /api/inventory/{barcode}          {"quantity": n} or {"delta": n}
GET    /api/events?limit=50&before=<id>  scan events, newest first
GET    /api/products/{barcode}
PUT    /api/products/{barcode}           {"name", "brand"}; sets source = manual
```

- `PATCH /inventory` takes exactly one of `quantity` or `delta`. The result is floored at 0. It creates the row if absent and writes a `scan_events` row with `source = 'manual'`, `action = 'adjust'`, a NULL nonce, and the delta actually applied, so the event log stays complete.
- `GET /events` returns events with `id < before` (or the newest if `before` is omitted), plus `next_before`: the smallest id returned, or `null` when there are no more. Because it pages by id, new scans arriving mid-browse don't shift the results.
- Timestamps are UTC RFC 3339; the page converts to local time.
- One error shape everywhere. Override FastAPI's default validation-error format to match section 4.

### 6.7 Product lookup

`GET https://world.openfoodfacts.org/api/v2/product/{barcode}.json` with a `User-Agent` naming the application and a contact email, as Open Food Facts requests.

Run via FastAPI `BackgroundTasks`, after the response is sent. The background task opens its own database connection; the request's connection is closed by the time it runs. Set an explicit `httpx` timeout (10s) rather than relying on the library default.

Cache results in `products`. Look up a barcode only if it has no row, its `name` is null, or `fetched_at` is over 90 days old. A row with `source = 'manual'` is never overwritten. If Open Food Facts has no match, store a row with `source = 'unknown'` so the page can prompt for a name.

### 6.8 Layout

```
server/
  app/
    main.py        app, token dependency, error handlers, static mount
    api.py         routes
    scans.py       scan handling (6.4)
    db.py          connection, all SQL, migration runner
    lookup.py      Open Food Facts
    cli.py         replay-events
  migrations/001_init.sql
  web/index.html
  tests/
  Dockerfile
  compose.yaml
scanner/
  src/
    main.c         poll loop, wiring
    frame.c/.h     framing (5.3)
    queue.c/.h     in-flight buffer (5.5)
    backoff.c/.h   retry timing (5.6)
    proto.c/.h     request JSON (snprintf), response parsing (cJSON)
    hw.h
    hw_linux.c     termios, libgpiod, libcurl, clock, random
    cJSON.c/.h     vendored
  tests/           unit tests for frame, queue, backoff, proto
  Makefile
  pantry-scanner.service
ops/
  backup-snapshot.sh
```

---

## 7. Web page

One static `index.html` served by the application. Vanilla JavaScript, `fetch()`, no framework, no build step.

On first visit it asks for the API token and keeps it in `localStorage`. It shows the inventory with +/− buttons, lets you name products the lookup couldn't identify, and shows recent events.

It uses only the JSON API. If the page needs something, it goes into the API, which keeps every feature testable with `curl`.

Deliberately plain.

---

## 8. Deployment

### Server

Docker Compose with `restart: unless-stopped`. The database lives on a host folder mounted into the container, so backup is a file copy. Pin the Python minor version in the Dockerfile.

```yaml
services:
  server:
    build: .
    restart: unless-stopped
    ports:
      - "8080:8080"
    volumes:
      - /srv/inventory/data:/data
    env_file: .env
    environment:
      DB_PATH: /data/inventory.db
```

`"8080:8080"` publishes the port on every interface, including `tailscale0`. That's intended: it lets you check the pantry from the store. Use a Tailscale ACL to limit port 8080 to your own devices.

### Scanner client

systemd unit on the Pi: `After=network-online.target`, `Wants=network-online.target`, `Restart=always`, `RestartSec=5`, `EnvironmentFile`. Not containerized; it needs direct access to the serial port and GPIO.

### Scanner path

The Pi reaches the server over the **plain LAN**, never over Tailscale. Both are on the same network; routing scans through a VPN adds something else that can break, for no benefit.

---

## 9. Backup

Backing up the live SQLite file directly is unreliable. In WAL mode, recent changes live in a separate file, so a copy taken mid-write can restore stale or corrupt. It usually works, which makes the failure easy to miss.

```bash
#!/usr/bin/env bash
set -euo pipefail
sqlite3 /srv/inventory/data/inventory.db \
  "VACUUM INTO '/srv/inventory/backup/inventory-snapshot.db'"
```

`VACUUM INTO` writes a consistent, self-contained copy while the database is in use. Run it on a systemd timer before restic runs, or as a restic pre-backup hook. `set -e` matters: without it, a failing snapshot goes unnoticed and restic keeps backing up an old file.

Point restic at the backup folder and **exclude the live database path**.

**Test the restore once:** while the server is writing, restore the snapshot to a scratch path and run `PRAGMA integrity_check`. Repeat after any change to the backup setup.

---

## 10. Security

**Present:** a shared API token compared in constant time; remote access only through Tailscale, limited by ACL; input validation at the API boundary; parameterized SQL everywhere.

**Absent on purpose:**

- **TLS.** On the home LAN, the token crosses the network unencrypted; over Tailscale it's encrypted by WireGuard. If you want HTTPS later, `tailscale serve` provides it with a valid certificate.
- **Per-person accounts.** Revisit if someone outside the household needs access, or if you want to know who changed what.
- **Rate limiting.** Nothing that can reach the server is untrusted enough to need it.

**Never port-forward.** Tailscale exists so that is unnecessary.

---

## 11. Out of scope

No user accounts or registration. No phone app. No microcontroller port. No LLM or AI features. No expiry-date tracking or shopping lists. No server-side mode or mode barcodes. No Bluetooth. No remote access outside Tailscale. No TLS. No durable client-side queue.

---

## 12. Acceptance criteria

**Protocol**

1. The same nonce posted twice produces exactly one inventory change; the second returns `duplicate`.
2. A batch containing one invalid element returns `invalid` for that element and still applies the others.
3. Any `/api/` request without the correct token returns 401 and no data.

**Inventory**

4. Quantity never falls below zero.
5. Removing an out-of-stock item returns `rejected_not_in_stock` and is visible in the event log.
6. A manual adjustment writes a `scan_events` row with `source = 'manual'`.
7. `replay-events` rebuilds `inventory` to a state identical to live.

**Scanner**

8. The switch position at scan time determines the action, regardless of its position at send time.
9. Stopping the server, scanning five items, and restarting it results in all five appearing.
10. A forced lost acknowledgement results in one inventory change, not two.
11. Exceeding 32 queued scans drops oldest-first, logs at ERROR, and signals on the buzzer.
12. Disconnecting and reconnecting the scan module recovers without restarting the process.
13. `termios`, `libgpiod`, and `libcurl` appear only in `hw_linux.c`, and the unit tests for `frame`, `queue`, `backoff`, and `proto` build and pass on a laptop.

**Operations**

14. Both services start on boot and recover from a restart of either, in any order.
15. `backup-snapshot.sh` produces a file passing `PRAGMA integrity_check` while the server is writing.

---

## 13. Build order

Each stage runs before the next begins.

1. Module configured for UART; barcodes printing in `miniterm`.
2. Server skeleton: schema, migration runner, `replay-events`. Write replay now, while it's trivial.
3. `POST /api/scans` with the token check and nonce deduplication. **Test a duplicate nonce with `curl` before going further.** This is the most important test in the project and the one most likely to look correct while being subtly broken.
4. Scanner logic on a laptop: `frame` and `proto` modules with unit tests. No Pi needed.
5. `hw_linux.c` and `main.c`: serial in, single-scan POST.
6. `GET /api/inventory` and the web page.
7. Switch and buzzer.
8. `queue` and `backoff` modules with unit tests, then wire them into the main loop.
9. Remaining endpoints; product lookup.
10. Docker, systemd, backup script, restore test.

Stages 1–6 make a working system.

Tests to write as you go: idempotency, quantity flooring, partial batch failure, buffer overflow, event replay. Those five are where the bugs will be.

---

## 14. Change log

**v2.0** — personal-scope rewrite of v1.0.

- **Removed:** the microcontroller-portability constraint, user accounts, refresh tokens, login rate limiting, the `devices` table, per-user inventory, phone-app API conventions, Postgres portability rules, and the deferred-security table for a shipped product.
- **Replaced:** separate device and user auth → one shared `API_TOKEN`. Portability seam → `hw.h`, which exists for laptop testing and now includes buzzer/LED control. `schema_version` table → `PRAGMA user_version`. General cursor pagination → `before=<id>` on events only. `/api/v1/` → `/api/`.
- **Fixed:** v1.0 required both whole-body Pydantic validation and partial-batch success, which contradict each other. Elements are now validated individually, with a new `invalid` result. `scan_rec_t.barcode` grows from 32 to 33 bytes so a 32-character barcode fits with its terminating NUL.
- **Kept:** wire protocol, nonce deduplication, the event log as the record of truth, the serial/framing/buffer/retry design, Docker, Tailscale, and backup.
