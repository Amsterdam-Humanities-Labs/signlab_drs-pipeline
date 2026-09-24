# signlab_drs-pipeline
The video pipeline on DRS, the studio Mac with the Sony FX30 cameras.
Operators: read [docs/manual.md](docs/manual.md) for the session checklist, file locations and troubleshooting.

## What it does
It moves raw camera files to storage and renders them in DaVinci Resolve. It then crops them with MediaPipe, converts them and uploads them to signcollect.nl.
```
import/ -> moveFiles -> studioFiles/YYYY-MM-DD/raw/ -> batch_queue (Resolve) -> post_noncropped/
        -> crop (MediaPipe, 1:1.15) -> post/ -> convertFiles (H.264 + thumbnail) -> signcollect.nl
```
Helpers:
- `listFiles`: file counts and glosId coverage, sent to signcollect.nl.
- `mouse` and `keyboardMonitor`: keep the Mac awake and record when the operator was last active.
- `networkManager`: checks ethernet and Tailscale. `qrScanner`: reads QR codes in raw clips.

## Where it runs
- DRS, user `signlab`, checkout at `/Users/signlab/drs`. The paths are hardcoded.
- Storage: rclone mounts `signcollect:` at `/Users/signlab/signCollect`, with its cache on `/Volumes/cacheDisk/rclone`.

## Status
Production.

## How to run
`startupScript.py` is the entry point. It starts and supervises the services below. Each service logs to `logs/<service>.log`, rotated at 10 MB.

| Service | Command |
|---|---|
| mouse, keyboardMonitor, moveFiles, convertFiles, listFiles | `/usr/bin/python3 services/<file>.py`: `mouse.py`, `keyboard_monitor.py`, `moveFiles.py`, `convertFiles.py`, `listFiles.py` |
| networkManager | `/usr/bin/python3 -u services/network_manager.py` |
| batch | `/usr/bin/python3 services/batch_queue.py` |
| crop | `/usr/bin/python3 services/crop.py` |
| rclone | `/Users/signlab/rclone/rclone mount signcollect: /Users/signlab/signCollect ...` |
| watchdog | `scripts/watchdog.sh`. Restarts `startupScript.py` if the PID in `startup.pid` dies. Rewritten on every launch. |
| qrScanner | `bin/python3 qr/qr_scanner_service.py`. Uses the venv in the repo root. `qr/` is not in git. |

```bash
cd /Users/signlab/drs
/usr/bin/python3 startupScript.py        # or inside: screen -S startup ...
```
- A service that crashes is restarted up to 5 times in 5 minutes. After that it is paused for 15 minutes.
- rclone starts only when DNS resolves and the mount point is unmounted and empty. Stray files are moved to quarantine first.
- An unhealthy mount is restarted, unless rclone is still writing to its log.
- Scheduled daily restarts are off (`restart_times = []`).
- `startupScript.py` does not start `services/startServer_beta.js` (commented out), `services/crop_fix.py`, `services/startMonitor.py` (a PyQt dashboard) or `services/qrConvert.py`.
- `scripts/batch.sh` runs `variants/batch.py` in a loop. Start it by hand.
- `tools/`: one-off repair and backfill scripts (the manual says when to use them). `variants/`: other batch and crop versions, run by hand. `test/`: tests, `python3 -m pytest test/` (runs off the DRS too); `test/manual_*.py` are scripts you run by hand.

## Configuration
- DB credentials for `services/qrConvert.py` and `tools/check_studiofiles.py`: `DB_*` environment variables or an untracked `.env` in the repo root. See `.env.example`.
- The rclone remote `signcollect:` lives in the rclone config of the `signlab` user (not in git).
- `/etc/sudoers.d/signlab-network` gives `network_manager.py` passwordless sudo.
- `mouse_config.json` in the repo root is optional. See `config/mouse_config.json.example`.
- DaVinci Fusion compositions are in `config/*.setting`.

## Dependencies
- DaVinci Resolve, through its scripting API (`shared/python_get_resolve.py`).
- signcollect.nl endpoints: `videoProc/upload2.php` and `videoProc/upload_post.php`, `renderServer`, `drs_ep/api.php` (render claims, via `shared/drs_render_client.py`), `listFiles.php` and `CR.php`.
- Heartbeats: `client_monitor_api/api.php` (via `shared/signcollect_monitor.py`), see [signlab_client_monitor_api](https://github.com/Amsterdam-Humanities-Labs/signlab_client_monitor_api).
- `services/crop_fix.py` reads the re-crop queue from `videoFix/crop_fixes.json` and reports to `videoFix/api.php` ([signlab_crop-fix-manager](https://github.com/Amsterdam-Humanities-Labs/signlab_crop-fix-manager)).
- FX30 camera controller: [signlab_Sony-SDK-MACOS-API](https://github.com/Amsterdam-Humanities-Labs/signlab_Sony-SDK-MACOS-API).
- Stack overview: [signlab_signcollect-stack](https://github.com/Amsterdam-Humanities-Labs/signlab_signcollect-stack).
