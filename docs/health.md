# Studio health check (`tools/health.py`)

One line per studio part: ok, warn or fail. The Status tab of the camera controller app shows it as lights; a sysadmin runs it over ssh.

```bash
/usr/bin/python3 /Users/signlab/drs/tools/health.py          # table; exit 0 ok, 1 warn, 2 fail
/usr/bin/python3 /Users/signlab/drs/tools/health.py --json   # one JSON object; exit 0
/usr/bin/python3 /Users/signlab/drs/tools/health.py --only cameras --only network
```

It is read-only: it lists processes and mounts, reads the ends of the logs and sends GET requests (`/api/status` on the camera server, the SignCollect server). It does not write, restart anything, connect to Resolve or command a camera. Every command and request has a timeout and the checks run side by side; the run is cut off at 10 seconds, and a check that is still busy then is reported `unknown`.

It needs only Python 3.9 and the standard library, and runs when copied alone into an older checkout (without `shared/server_config.py` or `.env`).

## JSON

```json
{"generated_at": "2026-10-07T14:02:11+02:00", "host": "signlabs-mini", "overall": "ok",
 "checks": [{"id": "research_drive", "title": "Research drive", "status": "ok",
             "detail": "mounted, 1.0 TB free on its cache disk", "action": "",
             "help_url": "https://amsterdam-humanities-labs.github.io/signlab_docs/studio/troubleshooting/#studio-research-drive"}]}
```

- `status`: `ok`, `warn`, `fail` or `unknown` (the check itself could not run; the reason is in `detail`).
- `overall`: the worst status. `unknown` counts as `warn`.
- `action`: what to do, empty when ok. `help_url`: the page in the docs.

## The checks

| id | ok when | warn | fail |
|---|---|---|---|
| `research_drive` | The rclone mount is mounted, its marker file `do_not_remove_for_rclone` is readable, rclone runs, and the disk with rclone's cache has 100 GB free | under 100 GB free | not mounted, not responding within 5 s, marker missing, rclone gone, or under 20 GB free |
| `cache_disk` | `/Volumes/cacheDisk` is mounted with 15% or more free | under 15% free | not mounted, or under 5% free |
| `resolve` | Resolve is installed with a Studio licence and the last finished run in `logs/batch.log` had no error | single files failed, the run was skipped because the drive was down, the batch waits for DNS or the drive, a run takes over 12 h, no output for over 2 h after a run, or no Studio licence file | not installed, or the run stopped: Resolve did not open, the project did not load, a batch failed, or an unexpected error |
| `pipeline` | The PID in `startup.pid` is `startupScript.py` and all 11 services run | a helper is down (mouse, keyboardMonitor, listFiles, networkManager, qrScanner, watchdog), or a log is silent too long: rclone and networkManager 15 min, moveFiles 60 min, crop and convertFiles 3 h | the start-up job is down, or rclone, moveFiles, batch, crop or convertFiles is down |
| `cameras` | `GET :8080/api/status` answers and all expected cameras are connected. Also ok while the server downloads or lists files (it disconnects the cameras for that) | some cameras connected, or a scan is running | the server does not answer, or no camera is connected |
| `qr_screen` | A Chrome process uses the profile `~/.signcollect-kiosk-chrome` (the controller app tests the same way) | the QR page is not open | never |
| `network` | A network port has a link and an address, Tailscale is connected and the SignCollect server answers | Tailscale is not connected or not installed; with `--require-ethernet`, the Mac is on Wi-Fi only | no network connection, or the server does not answer (or answers 5xx) |
| `uploads` | No `.upload_failed` marker is older than a day and the recent logs have no upload error | a marker from a recording more than 1 day old, or upload errors in the ends of `convertFiles.log` and `crop.log` | a marker from a recording more than 3 days old |
| `crop_fixes` | `GET <server>/videoFix/crop_fixes.json` with `X-Api-Token` returns 200 | no `VIDEOFIX_TOKEN` configured (no request is sent), or another HTTP status | 401 or 403 |

Notes:

- Research drive, free space: the mount itself reports 0 bytes free even when it works, so the check looks at the disk rclone buffers its writes on (`--cache-dir` on the rclone command line). `detail` also gives the number of files rclone still has to send, from the last line of `logs/rclone.log`.
- Resolve is judged from the batch log, not from the process: the batch starts and kills Resolve every hour. While a run is busy, the status is that of the run before it.
- Cameras, expected number: `--cameras N`, else `expected_cameras` in the controller app's `config.json` (`~/Sony-SDK-MACOS-API` or `~/signlab_Sony-SDK-MACOS-API`), else the number of cameras in the status.
- Network: the studio normally runs on Wi-Fi with the ethernet cable out, so Wi-Fi alone is ok; `detail` says which one is in use.
- Uploads: `convertFiles.py` and `crop.py` leave `<video>.upload_failed` next to a file whose upload failed, in `studioFiles/<date>/converted/` and `post/`, and retry it every cycle. A marker that holds the line `uploaded` is done. The check lists only the folders of the last 30 recording days (`--upload-days`) in one `find` with a timeout. A marker is rewritten on every retry, so its age is the date of its folder. Uploads the server refused leave no marker, only a log line; the logs have no timestamps, so "recent" is the last 256 KB of `convertFiles.log` and 2 MB of `crop.log`, a few hours.

## Options

`--pipeline-dir`, `--mount`, `--cache-disk`, `--camera-url`, `--server-url`, `--cameras N`, `--upload-days N`, `--require-ethernet`, `--only ID`. The paths also read `DRS_HEALTH_PIPELINE_DIR`, `DRS_HEALTH_MOUNT`, `DRS_HEALTH_CACHE_DISK` and `DRS_HEALTH_CAMERA_URL`. The server and token come from `SIGNCOLLECT_URL` and `VIDEOFIX_TOKEN` (environment or `.env`), through `shared/server_config.py` when the checkout has it.

## Add a check

1. Write `check_name(env)` in `tools/health.py`. Return `result(status, detail, action)`. Use only `env` for the outside world: `env.run(cmd, timeout)` for commands, `env.http_get(url, headers, timeout)` for GET requests, `env.now()` for the time, and the paths on `env`. Touch the mount only through `env.run`, so a hung mount cannot hang the check. Do not catch every error: the runner turns an exception into `unknown`.
2. Add one line to `CHECKS`: id, title, docs anchor, function.
3. Add tests in `test/test_health.py` for ok, warn, fail and unknown, with the `Fakes` class. Run `python3 -m pytest test/test_health.py`.
4. Add a row to the table above.
