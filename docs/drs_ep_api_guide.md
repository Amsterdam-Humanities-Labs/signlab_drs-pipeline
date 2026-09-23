# DRS Render Coordination API

## Endpoint: `https://signcollect.nl/drs_ep/api.php`

## Purpose
Coordinates rendering between multiple DaVinci Resolve machines so they don't render the same files. Uses a JSON file with file locking — no database needed.

## Setup

1. Create directory `drs_ep/` on the server
2. Upload `api.php` (the PHP source is not in this repo)
3. Make sure the directory is writable by the web server: `chmod 775 drs_ep/`
4. Done — `render_claims.json` is created automatically on first use

```
signcollect.nl/
└── drs_ep/
    ├── api.php              ← the PHP file
    ├── render_claims.json   ← auto-created, stores all claims
    └── render_claims.json.lock  ← auto-created, for atomic access
```

## How It Works

- Data stored in `render_claims.json` (auto-created)
- All writes use `flock()` for atomic access (prevents race conditions between two machines hitting the API simultaneously)
- Stale claims from crashed machines can be cleaned up via the `cleanup` action

## API Actions

All actions via `?action=<name>`. POST actions accept JSON body.

### `POST ?action=claim` — Claim a single file

```json
// Request
{"filename": "M20260216_4332.MP4", "machine": "mac-studio"}

// Response (success)
{"success": true, "message": "File claimed"}

// Response (already claimed)
{"success": false, "message": "Already claimed by mac-signlab", "claimed_by": "mac-signlab"}
```

### `POST ?action=claim_batch` — Claim multiple files

```json
// Request
{"filenames": ["M20260216_4332.MP4", "L20260216_5915.MP4"], "machine": "mac-studio"}

// Response
{"success": true, "claimed": ["M20260216_4332.MP4"], "already_claimed": ["L20260216_5915.MP4"]}
```

### `POST ?action=complete` — Mark file rendered

```json
{"filename": "M20260216_4332.MP4", "machine": "mac-studio"}
```

### `POST ?action=complete_batch` — Mark multiple files rendered

```json
{"filenames": ["M20260216_4332.MP4", "R20260216_8767.MP4"], "machine": "mac-studio"}
```

### `POST ?action=release` — Release claim (render failed)

```json
{"filename": "M20260216_4332.MP4", "machine": "mac-studio"}
```

### `POST ?action=release_batch` — Release multiple claims

```json
{"filenames": ["M20260216_4332.MP4"], "machine": "mac-studio"}
```

### `GET ?action=get_active` — List files being rendered now

```json
{"success": true, "claims": [
    {"filename": "M20260216_4332.MP4", "machine": "mac-studio", "claimed_at": "2026-02-17 12:00:00"}
]}
```

### `GET ?action=check&filename=M20260216_4332.MP4` — Check single file

```json
{"success": true, "claimed": true, "claimed_by": "mac-studio", "claimed_at": "2026-02-17 12:00:00"}
```

### `GET ?action=get_completed&date=2026-02-16` — List completed files

### `GET ?action=stats` — Per-machine statistics

```json
{"success": true, "stats": {"mac-studio": {"claimed": 5, "completed": 120}, "mac-signlab": {"claimed": 3, "completed": 85}}}
```

### `POST ?action=cleanup` — Release stale claims from crashed machines

```json
// Request
{"max_age_minutes": 60}

// Response
{"success": true, "released": 3, "message": "Released 3 stale claims"}
```

## Coordination Flow

```
Mac-Studio                          API                         Mac-Signlab
    |                                |                               |
    |-- claim_batch([A,B,C,D]) ----->|                               |
    |<-- claimed: [A,B] -------------|                               |
    |                                |<-- claim_batch([C,D,E,F]) ----|
    |                                |--- claimed: [C,D,E,F] ------->|
    |   (renders A,B)                |                (renders C,D,E,F)
    |-- complete_batch([A,B]) ------>|                               |
    |                                |<-- complete_batch([C,D,E,F])--|
```

## Python Client

Use `drs_render_client.py` in this repo:

```python
from drs_render_client import DRSRenderClient

client = DRSRenderClient(machine_name="mac-studio")

# Before rendering a batch, claim the files
filenames = ["M20260216_4332.MP4", "L20260216_5915.MP4"]
claimed = client.claim_files(filenames)
# claimed = ["M20260216_4332.MP4"]  — only render these

# After rendering
client.complete_files(claimed)

# Periodically clean up stale claims from crashed machines
client.cleanup_stale(max_age_minutes=60)
```
