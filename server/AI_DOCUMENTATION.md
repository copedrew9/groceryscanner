# Pantry inventory — server

FastAPI + SQLite. Spec: `../SPEC-v2_0-pantry-inventory.md` (v2.0 is authoritative).

```
app/main.py     app, config, token dependency, error handlers, static mount
app/api.py      routes
app/scans.py    scan handling (hand-written)
app/db.py       connection, migrations, all SQL
app/lookup.py   Open Food Facts
app/cli.py      replay-events
migrations/     numbered .sql, tracked with PRAGMA user_version
web/index.html  the page
tests/
```

## Configuration

Read from the environment, or from `.env` when running under Compose. Copy
`.env.example` to `.env` and fill it in.

| Variable | Required | Meaning |
|---|---|---|
| `API_TOKEN` | yes | Shared secret. The server refuses to start if it is missing or under 32 characters. |
| `DB_PATH` | no | Database file. Defaults to `inventory.db` in the working directory. Compose sets it to `/data/inventory.db`. |
| `CONTACT_EMAIL` | no | Sent to Open Food Facts in the `User-Agent`. **Product lookups are skipped entirely while this is unset**, so names never appear. |

## Generate a token

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

The same value goes in three places: the server's `.env`, the scanner's
`.env`, and the web page (typed in once, kept in `localStorage`). Changing it
means updating all three and restarting both services.

## Run locally

```bash
pip install -r requirements.txt
export API_TOKEN=$(python -c "import secrets; print(secrets.token_hex(32))")
export DB_PATH=./inventory.db
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

The page is at <http://127.0.0.1:8080/>. Migrations run at startup, so there
is no separate setup step.

Check it from the command line:

```bash
curl -s localhost:8080/api/inventory -H "Authorization: Bearer $API_TOKEN"
curl -s -X PATCH localhost:8080/api/inventory/041196891010 \
  -H "Authorization: Bearer $API_TOKEN" -H "Content-Type: application/json" \
  -d '{"delta": 1}'
```

## Run the tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests -q
```

No test touches the network or a real database file; each gets a temp one.

## replay-events

Rebuilds `inventory` from `scan_events`, which is the record of truth. Safe to
run any time; it is a no-op if the two already agree.

```bash
DB_PATH=./inventory.db python -m app.cli replay-events
```

Under Compose:

```bash
docker compose exec server python -m app.cli replay-events
```

## Deploy with Compose

The database lives on the host at `/srv/inventory/data`, so a backup is a file
copy.

```bash
sudo mkdir -p /srv/inventory/data
cp .env.example .env    # then fill it in
docker compose up -d --build
docker compose logs -f
```

Port 8080 is published on every interface, including `tailscale0` — that is
what makes the pantry readable from the store. Restrict it to your own devices
with a Tailscale ACL. **Never port-forward.**

One uvicorn worker, deliberately: several processes writing one SQLite file
produce intermittent `database is locked` errors.

## Backup

`../ops/backup-snapshot.sh` writes a consistent snapshot with `VACUUM INTO`
while the server is running. Point restic at the backup folder and **exclude
the live database path**.

```bash
sudo install -m 755 ../ops/backup-snapshot.sh /srv/inventory/ops/backup-snapshot.sh
sudo cp ../ops/pantry-backup.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pantry-backup.timer
systemctl list-timers pantry-backup.timer
```

Test the restore once, while the server is writing:

```bash
sudo systemctl start pantry-backup.service
sqlite3 /srv/inventory/backup/inventory-snapshot.db "PRAGMA integrity_check;"
```

## Errors

One shape everywhere. Clients switch on `code`, never on message text.

```json
{"error": {"code": "invalid_token", "message": "Missing or incorrect token"}}
```

`invalid_token` (401), `invalid_request` (422), `not_found` (404).
