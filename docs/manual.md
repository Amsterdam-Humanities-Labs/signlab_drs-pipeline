# DRS operator manual

DRS is the studio Mac (user `signlab`) with the Sony FX30 cameras on USB. It records with `fx30MultiRecord` and runs the video pipeline in this repo.
Shorthand in this manual: `~/drs` = `/Users/signlab/drs`; `$SF` = `/Users/signlab/signCollect/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles`.

## What runs on DRS

| Process | What it does | How often | Heartbeat id |
|---|---|---|---|
| `fx30MultiRecord` ([signlab_Sony-SDK-MACOS-API](https://github.com/Amsterdam-Humanities-Labs/signlab_Sony-SDK-MACOS-API)) | Starts and stops all FX30s and downloads clips. Dashboard at `http://localhost:8080` | On demand. Started by hand; nothing supervises it | - |
| `fx30_controller.py` (PyQt app) | Downloads clips to staging, sorts them by date, then runs `rclone copy` to `$SF` | On demand. TODO: confirm where its code lives (not in any repo) | - |
| `startupScript.py` | Starts and supervises everything below. Log: `~/drs/startup.log` | 5 s loop | - |
| `scripts/watchdog.sh` | Restarts `startupScript.py` if the PID in `~/drs/startup.pid` is dead | 30 s | - |
| `rclone` | Mounts `signcollect:` at `~/signCollect`, with its cache in `/Volumes/cacheDisk/rclone` | Continuous | - |
| `moveFiles` | Fills gaps: copies staging `raw/` files that the controller missed to `$SF/<date>/raw/` | 15 min | `drs-file-mover` |
| `batch` (`batch_queue.py`) | Renders in DaVinci Resolve: up to 50 clips per batch, from the last 62 days | 1 h | `drs-batch-queue` |
| `crop` | MediaPipe crop, H.264 and thumbnail, then uploads to `videoProc/upload_post.php` | 15 min | `drs-crop-processor` |
| `convertFiles` | Converts raw to H.264 with a thumbnail, then uploads to `videoProc/upload2.php` | 15 min | `drs-converter` |
| `listFiles` | Sends file counts and glosId coverage per day to `listFiles.php` | 1 h | `drs-file-lister` |
| `networkManager` | Checks ethernet, internet and Tailscale, and recovers them | 30 s | `drs-network-manager` |
| `qrScanner` (`qr/`, not in git) | Reads the QR code in raw clips, writes `raw/<clip>.json` and posts to `qr/qrResultReceiver.php` | TODO: confirm | TODO: confirm |
| `mouse`, `keyboardMonitor` | Keep macOS awake and record the last keypress | Continuous | - |

Heartbeats go to `https://signcollect.nl/client_monitor_api/api.php`. See [signlab_client_monitor_dashboard](https://github.com/Amsterdam-Humanities-Labs/signlab_client_monitor_dashboard).

## Session checklist

### Before recording
- [ ] `ps -p $(cat ~/drs/startup.pid)` shows `startupScript.py`. If not, see [How to run](../README.md#how-to-run).
- [ ] `ls "$SF/../do_not_remove_for_rclone"` succeeds. This means the mount is up.
- [ ] `/Volumes/cacheDisk` is mounted and has free space.
- [ ] `http://localhost:8080` lists every camera. If not, send `POST /api/scan`, then `POST /api/reset`.
- [ ] Only one `fx30MultiRecord` is running (`pgrep -fl fx30MultiRecord`).

### During recording
- [ ] Start and stop takes from Camera Control in [signlab_camera-control](https://github.com/Amsterdam-Humanities-Labs/signlab_camera-control). It forwards the requests to `:8080`.
- [ ] Do not use DaVinci Resolve on DRS by hand. `batch` runs `pkill -9 -f 'DaVinci Resolve'` at the start and end of every batch.
- [ ] Do not start a second `fx30MultiRecord`. Only one process can hold the cameras on USB.

### After recording
- [ ] Download clips to `/Volumes/cacheDisk/fx30_staging/inbox`. Never download into `~/signCollect`: the mount reports 0 bytes free.
- [ ] Clips appear in `fx30_staging/<date>/raw/` and then in `$SF/<date>/raw/`. Check with `rclone lsf -R` on the remote, because the directory cache of the mount can be up to 2 h out of date.
- [ ] Leave staging alone. The controller treats it as the source of truth, and `moveFiles` never deletes from it. TODO: confirm cleanup policy.
- [ ] Wait for the next `batch` run (hourly) and `crop`/`convertFiles` runs (every 15 min). Then `post_noncropped/`, `post/`, `converted/` and `thumbnails/` fill for that date.

## Where files go

| Stage | Path | Written by |
|---|---|---|
| Camera download | `/Volumes/cacheDisk/fx30_staging/inbox/` | fx30 controller |
| Staging, by date | `/Volumes/cacheDisk/fx30_staging/<YYYY-MM-DD>/raw/` | fx30 controller |
| Raw, on the research drive | `$SF/<YYYY-MM-DD>/raw/<L/M/R/A/B><YYYYMMDD>_<HHMM>.MP4` | controller (`rclone copy`), `moveFiles` |
| QR result | `$SF/<date>/raw/<clip>.json` = `[glosId, type, time]` | `qrScanner` |
| Plain H.264 and thumbnail | `$SF/<date>/converted/`, `$SF/<date>/thumbnails/<clip>.jpg` | `convertFiles` |
| Resolve working folders | `~/drs/import/`, `~/drs/export/` (emptied every batch) | `batch` |
| Rendered | `$SF/<date>/post_noncropped/<clip>.mp4`. On failure: `<clip>.skip` | `batch` |
| Cropped | `$SF/<date>/post/<clip>_h264.mp4` and `<clip>.jpg`. On failure: `post_noncropped/<clip>_error.json` | `crop` |
| Crop temp files | `~/drs/temp/` (files older than 1 h are removed; capped at 10 GB) | `crop` |
| Logs | `~/drs/logs/<service>.log` (rotated at 10 MB), `~/drs/startup.log`, `~/drs/watchdog.log` | all |
| Quarantine | `/Users/signlab/signCollect_stray_<timestamp>/` | `startupScript.py` |

The pipeline routes each clip by the date in its file name, not by the folder it is in.

## Supervision rules (`startupScript.py`)

| Rule | Value |
|---|---|
| Crashed service | Restarted, at most 5 times in 5 min. After that, no restarts for 15 min. |
| Mount check | Every 60 s, using the marker file. Skipped for 30 min after rclone starts or restarts. |
| rclone restart | Only if DNS for `uva.data.surf.nl` resolves and `logs/rclone.log` has been silent for 120 s. |
| rclone start | Only after DNS resolves, all stacked mounts are force-unmounted and the mount point is empty. |
| Stop | SIGTERM. Waits 90 s for rclone and 10 s for the others, then sends SIGKILL. |
| Scheduled restarts | Off (`restart_times = []`). |
| Monitor loop | After 10 internal errors in a row it exits and stops all services, including the watchdog. |
| Watchdog | Relaunches `startupScript.py` only if it died without cleaning up (crash, SIGKILL). |

## Troubleshooting

| Symptom | Check | Fix |
|---|---|---|
| Nothing is processed, no heartbeats | `ps -p $(cat ~/drs/startup.pid)`; `tail ~/drs/watchdog.log` | Start `startupScript.py` (see README). A clean Ctrl-C or SIGTERM also stops the watchdog. TODO: confirm auto-start at login |
| One service has stopped | `grep "Disabling restarts" ~/drs/startup.log`; `tail ~/drs/logs/<service>.log` | Fix the cause shown in its log. It restarts after the 15 min cooldown, or restart `startupScript.py` |
| `~/signCollect` is empty, "rclone marker file not found" | `tail ~/drs/logs/rclone.log`; `mount \| grep signCollect` | Wait for the 30 min grace period or the active upload to end. rclone restarts by itself when the rules above allow it |
| "Not starting rclone: WebDAV host does not resolve" or "DNS is down" | `tail ~/drs/logs/networkManager.log` | networkManager restarts ethernet and Tailscale. If it says "may need manual intervention", fix the network |
| networkManager: "Passwordless sudo not configured" | Does `/etc/sudoers.d/signlab-network` exist? | Restore the sudoers rule and restart `startupScript.py` |
| "Refusing to start rclone: mount point still mounted" | `mount \| grep signCollect` | The FUSE mount is dead. TODO: confirm (manual `umount -f` or reboot) |
| CRITICAL "Mount point ... was not empty ... moved to ..." | `ls ~/signCollect_stray_*` | These files exist only there. Copy them onto the mount once it is up, then delete the quarantine folder |
| moveFiles: "Source directory not found" | `ls /Volumes/cacheDisk/fx30_staging` | Mount or attach `cacheDisk` |
| Clip is in staging but not in `raw/` | moveFiles log: zero-byte or in-flight file | Zero-byte files are never copied. Files younger than 10 min wait for the next 15 min run |
| batch: "Failed to open DaVinci Resolve" or "project not loaded" | `logs/batch.log`; start Resolve by hand | Fix Resolve. The next hourly run tries again |
| Clip is never rendered | `post_noncropped/<clip>.skip` (contains the reason) | Fix the cause, delete the `.skip` file and wait for the next run |
| crop `_error.json` says `pose_detection_failed` | Is a person in frame? | Every run tries again (the error JSON does not stop retries). TODO: confirm fix |
| crop: "Date in filename ... does not match date in folder" | Compare the folder with `<L/M/R><YYYYMMDD>` | Move the clip to the folder for its date |
| Cropped video or thumbnail is missing on the web | crop log says "Upload failed" | crop never retries uploads. Run `python3 tools/backfill_post_uploads.py <date> --dry-run`, then run it without `--dry-run` |
| Clip has a QR JSON but is not in [signlab_studio-archive](https://github.com/Amsterdam-Humanities-Labs/signlab_studio-archive) | qrScanner could not reach the API | Run `python3 tools/replay_qr_results.py <date> --dry-run`, then run it without `--dry-run` |
| QR JSON is empty (`[]`) or missing | `raw/<clip>.json` | Run `python3 tools/qr_backfill.py "$SF/<date>/raw" --dry-run`. Add `--send-api` to send the results |
| Cameras missing on `:8080` | Dashboard, USB cables | Send `POST /api/scan`, then `POST /api/reset`. If the API is down, start it by hand (see runbook) |
| Screen locks or the Mac sleeps | `logs/mouse.log` | Check `~/drs/mouse_config.json` (see `config/mouse_config.json.example`) |

More: the [stack runbook](https://github.com/Amsterdam-Humanities-Labs/signlab_signcollect-stack/blob/main/docs/runbook.md) and [machines.md#drs](https://github.com/Amsterdam-Humanities-Labs/signlab_signcollect-stack/blob/main/docs/machines.md#drs).
