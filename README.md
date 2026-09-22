# signlab_drs
Video pipeline on the DRS Mac: moves raw camera files to storage, renders them in DaVinci Resolve, crops with MediaPipe, converts and uploads to signcollect.nl.

## What it does
```
import/ -> moveFiles -> studioFiles/YYYY-MM-DD/raw/ -> batch_queue (Resolve) -> post_noncropped/
        -> crop (MediaPipe, 1:1.15) -> post/ -> convertFiles (H.264 + thumbnail) -> signcollect.nl
```
Helpers: `listFiles` (file counts and glosId coverage to signcollect.nl), `mouse`/`keyboardMonitor` (keep awake, operator idle), `networkManager` (ethernet/Tailscale), `qrScanner`.

## Where it runs
- DRS Mac, user `signlab`, checkout at `/Users/signlab/drs` (paths are hardcoded).
- Storage: rclone mount `signcollect:` at `/Users/signlab/signCollect`, cache on `/Volumes/cacheDisk/rclone`.

## Status
Production.

## How to run
`startupScript.py` is the entry point. It starts and supervises these services (logs in `logs/<service>.log`, rotated at 10 MB):

| Service | Command |
|---|---|
| mouse, keyboardMonitor, moveFiles, convertFiles, listFiles, networkManager | `/usr/bin/python3 services/<script>.py` |
| batch | `/usr/bin/python3 services/batch_queue.py` |
| crop | `/usr/bin/python3 services/crop.py` |
| rclone | `/Users/signlab/rclone/rclone mount signcollect: /Users/signlab/signCollect ...` |
| watchdog | `scripts/watchdog.sh` (restarts `startupScript.py` if the PID in `startup.pid` dies; rewritten on every launch) |
| qrScanner | `bin/python3 qr/qr_scanner_service.py` (repo-root venv; `qr/` is not in git) |

```bash
cd /Users/signlab/drs
/usr/bin/python3 startupScript.py        # or inside: screen -S startup ...
```
- A crashed service is restarted, at most 5 times in 5 min, then paused for 15 min.
- rclone is only started once DNS resolves and the mount point is unmounted and empty (stray files are quarantined); an unhealthy mount is restarted unless rclone is still writing its log.
- Scheduled daily restarts are disabled (`restart_times = []`).
- Not started by `startupScript.py`: `services/startServer_beta.js` (commented out), `services/crop_fix.py`, `services/startMonitor.py` (PyQt dashboard), `services/qrConvert.py`. `scripts/batch.sh` loops `variants/batch.py` by hand.

## Configuration
- rclone remote `signcollect:` in the `signlab` user's rclone config (not in git).
- `/etc/sudoers.d/signlab-network`: passwordless sudo for `network_manager.py`.
- `mouse_config.json` in the repo root (optional, see `config/mouse_config.json.example`).
- DaVinci Fusion compositions in `config/*.setting`.

## Dependencies
- DaVinci Resolve (scripting API via `shared/python_get_resolve.py`).
- signcollect.nl: `videoProc/upload*.php`, `renderServer`, `drs_ep/api.php` (render claims, `shared/drs_render_client.py`), `listFiles.php`, `CR.php`.
- FX30 camera controller: [signlab_Sony-SDK-MACOS-API](https://github.com/Amsterdam-Humanities-Labs/signlab_Sony-SDK-MACOS-API).
- Stack overview: https://github.com/Amsterdam-Humanities-Labs/signlab_signcollect-stack
