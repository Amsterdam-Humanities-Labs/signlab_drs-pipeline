# DRS operator manual

DRS = the studio Mac (user `signlab`) with the Sony FX30 cameras on USB. It records (fx30MultiRecord) and runs the video pipeline in this repo.
Shorthand below: `~/drs` = `/Users/signlab/drs`; `$SF` = `/Users/signlab/signCollect/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles`.

## What runs on it

| Process | Does | Cycle | Heartbeat id |
|---|---|---|---|
| `fx30MultiRecord` ([Sony-SDK-MACOS-API](https://github.com/Amsterdam-Humanities-Labs/signlab_Sony-SDK-MACOS-API)) | Start/stop all FX30s, download clips; dashboard `http://localhost:8080` | on demand; **no supervisor**, started by hand | - |
| `fx30_controller.py` (PyQt app) | Downloads clips to staging, sorts by date, `rclone copy` to `$SF` | on demand; TODO: confirm where its code lives (not in any repo) | - |
| `startupScript.py` | Starts and supervises everything below; log `~/drs/startup.log` | 5 s loop | - |
| `scripts/watchdog.sh` | Restarts `startupScript.py` if the PID in `~/drs/startup.pid` is dead | 30 s | - |
| `rclone` | Mounts `signcollect:` at `~/signCollect` (cache `/Volumes/cacheDisk/rclone`) | continuous | - |
| `moveFiles` | Gap-filler: copies staging `raw/` files the controller missed to `$SF/<date>/raw/` | 15 min | `drs-file-mover` |
| `batch` (`batch_queue.py`) | DaVinci Resolve render, up to 50 clips per batch, last 62 days | 1 h | `drs-batch-queue` |
| `crop` | MediaPipe crop + H.264 + thumbnail, upload to `videoProc/upload_post.php` | 15 min | `drs-crop-processor` |
| `convertFiles` | raw -> H.264 + thumbnail, upload to `videoProc/upload2.php` | 15 min | `drs-converter` |
| `listFiles` | Per-day file counts + glosId coverage -> `listFiles.php` | 1 h | `drs-file-lister` |
| `networkManager` | Ethernet/internet/Tailscale checks and recovery | 30 s | `drs-network-manager` |
| `qrScanner` (`qr/`, not in git) | Reads QR in raw clips, writes `raw/<clip>.json`, posts to `qr/qrResultReceiver.php` | TODO: confirm | TODO: confirm |
| `mouse`, `keyboardMonitor` | Keep macOS awake; record last keypress | continuous | - |

Heartbeats go to `https://signcollect.nl/client_monitor_api/api.php` (see signlab_client_monitor_dashboard).

## Session checklist

**Before recording**
- [ ] `ps -p $(cat ~/drs/startup.pid)` shows `startupScript.py`; if not, see [How to run](../README.md#how-to-run).
- [ ] `ls "$SF/../do_not_remove_for_rclone"` succeeds (mount is up).
- [ ] `/Volumes/cacheDisk` is mounted and has free space.
- [ ] `http://localhost:8080` lists every camera; if not, `POST /api/scan`, then `POST /api/reset`.
- [ ] Only one `fx30MultiRecord` running (`pgrep -fl fx30MultiRecord`).

**During recording**
- [ ] Start/stop takes from studio_beta Camera Control (it proxies to `:8080`).
- [ ] Do not use DaVinci Resolve on DRS by hand: `batch` runs `pkill -9 -f 'DaVinci Resolve'` at start and end of every batch.
- [ ] Do not start a second `fx30MultiRecord`; the cameras are exclusive USB devices.

**After recording**
- [ ] Download clips to `/Volumes/cacheDisk/fx30_staging/inbox`, never into `~/signCollect` (mount reports 0 bytes free).
- [ ] Clips appear in `fx30_staging/<date>/raw/` and then `$SF/<date>/raw/`. Check with `rclone lsf -R` on the remote: the mount's directory cache is up to 2 h stale.
- [ ] Leave staging alone: the controller treats it as authoritative and `moveFiles` never deletes from it. TODO: confirm cleanup policy.
- [ ] After the next `batch` (hourly) and `crop`/`convertFiles` (15 min) cycles: `post_noncropped/`, `post/`, `converted/`, `thumbnails/` fill for that date.

## Where files go

| Stage | Path | Written by |
|---|---|---|
| Camera download | `/Volumes/cacheDisk/fx30_staging/inbox/` | fx30 controller |
| Staging by date | `/Volumes/cacheDisk/fx30_staging/<YYYY-MM-DD>/raw/` | fx30 controller |
| Raw on research drive | `$SF/<YYYY-MM-DD>/raw/<L/M/R/A/B><YYYYMMDD>_<HHMM>.MP4` | controller (`rclone copy`), `moveFiles` |
| QR result | `$SF/<date>/raw/<clip>.json` = `[glosId, type, time]` | `qrScanner` |
| Plain H.264 + thumbnail | `$SF/<date>/converted/`, `$SF/<date>/thumbnails/<clip>.jpg` | `convertFiles` |
| Resolve working dirs | `~/drs/import/`, `~/drs/export/` (wiped every batch) | `batch` |
| Rendered | `$SF/<date>/post_noncropped/<clip>.mp4`; failure marker `<clip>.skip` | `batch` |
| Cropped | `$SF/<date>/post/<clip>_h264.mp4` + `<clip>.jpg`; failure `post_noncropped/<clip>_error.json` | `crop` |
| Crop temp | `~/drs/temp/` (files > 1 h removed, capped at 10 GB) | `crop` |
| Logs | `~/drs/logs/<service>.log` (rotated at 10 MB), `~/drs/startup.log`, `~/drs/watchdog.log` | all |
| Quarantine | `/Users/signlab/signCollect_stray_<timestamp>/` | `startupScript.py` |

Clips are routed by the date in the filename, not the folder they sit in.

## Supervision rules (startupScript.py)

| Rule | Value |
|---|---|
| Crashed service | restarted; max 5 restarts in 5 min, then no restarts for 15 min |
| Mount check | every 60 s via the marker file; skipped for 30 min after rclone (re)starts |
| rclone restart | only if DNS for `uva.data.surf.nl` resolves **and** `logs/rclone.log` was silent for 120 s |
| rclone start | only after DNS resolves, all stacked mounts are force-unmounted and the mount point is empty |
| Stop | SIGTERM; waits 90 s for rclone, 10 s for others, then SIGKILL |
| Scheduled restarts | disabled (`restart_times = []`) |
| Monitor loop | exits after 10 consecutive internal errors and stops all services, watchdog included |
| Watchdog | relaunches `startupScript.py` only if it died without cleanup (crash, SIGKILL) |

## Troubleshooting

| Symptom | Check | Fix |
|---|---|---|
| Nothing processes, no heartbeats | `ps -p $(cat ~/drs/startup.pid)`; `tail ~/drs/watchdog.log` | Start `startupScript.py` (README). A clean Ctrl-C/SIGTERM stops the watchdog too. TODO: confirm auto-start at login |
| One service stopped | `grep "Disabling restarts" ~/drs/startup.log`; `tail ~/drs/logs/<service>.log` | Fix the cause in its log; it restarts after the 15 min cooldown, or restart `startupScript.py` |
| `~/signCollect` empty, "rclone marker file not found" | `tail ~/drs/logs/rclone.log`; `mount \| grep signCollect` | Wait out the 30 min grace / active upload; rclone is restarted automatically when the rules above allow |
| "Not starting rclone: WebDAV host does not resolve" / "DNS is down" | `tail ~/drs/logs/networkManager.log` | networkManager restarts ethernet and Tailscale; if it says "may need manual intervention", fix the network |
| networkManager: "Passwordless sudo not configured" | `/etc/sudoers.d/signlab-network` exists | Restore the sudoers rule, restart `startupScript.py` |
| "Refusing to start rclone: mount point still mounted" | `mount \| grep signCollect` | Dead FUSE mount. TODO: confirm (manual `umount -f` or reboot) |
| CRITICAL "Mount point ... was not empty ... moved to ..." | `ls ~/signCollect_stray_*` | These files exist only there: copy them onto the mount once it is up, then delete the quarantine dir |
| moveFiles "Source directory not found" | `ls /Volumes/cacheDisk/fx30_staging` | Mount/attach `cacheDisk` |
| Clip in staging but not in `raw/` | moveFiles log: zero-byte / in-flight | Zero-byte files are never copied; files < 10 min old wait for the next 15 min cycle |
| batch "Failed to open DaVinci Resolve" / "project not loaded" | `logs/batch.log`; Resolve starts by hand | Fix Resolve; next hourly run retries |
| Clip never rendered | `post_noncropped/<clip>.skip` (holds reason) | Fix the cause, delete the `.skip`, wait for the next run |
| crop `_error.json` `pose_detection_failed` | Is a person in frame? | Retried every run (error JSON is not a skip marker). TODO: confirm fix |
| crop "Date in filename ... does not match date in folder" | Folder vs `<L/M/R><YYYYMMDD>` | Move the clip to the matching date folder |
| Cropped video/thumbnail missing on the web | crop log "Upload failed" | crop never retries uploads: `python3 tools/backfill_post_uploads.py <date> --dry-run`, then without `--dry-run` |
| Clip has QR JSON but is not on studioIndex | qrScanner could not reach the API | `python3 tools/replay_qr_results.py <date> --dry-run`, then without |
| QR JSON empty `[]` or missing | `raw/<clip>.json` | `python3 tools/qr_backfill.py "$SF/<date>/raw" --dry-run`; add `--send-api` to push |
| Cameras missing on `:8080` | Dashboard, USB cables | `POST /api/scan`, then `POST /api/reset`; if the API is down start it by hand (runbook) |
| Screen locks / Mac sleeps | `logs/mouse.log` | Check `~/drs/mouse_config.json` (see `config/mouse_config.json.example`) |

More: [stack runbook](https://github.com/Amsterdam-Humanities-Labs/signlab_signcollect-stack/blob/main/docs/runbook.md), [machines.md#drs](https://github.com/Amsterdam-Humanities-Labs/signlab_signcollect-stack/blob/main/docs/machines.md#drs).
