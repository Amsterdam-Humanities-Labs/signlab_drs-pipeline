#!/usr/bin/env python3
"""Studio health check for the DRS Mac: one ok / warn / fail line per studio part.

    python3 tools/health.py           text table, exit 0 ok / 1 warn / 2 fail
    python3 tools/health.py --json    one JSON object, exit 0

Read-only: it lists processes and mounts, reads log tails, and sends GET
requests. It never writes, restarts anything, talks to Resolve or commands a
camera. Standard library only and Python 3.9, so it runs on /usr/bin/python3
and also when copied alone into an older checkout (no shared/server_config.py).

Each check is a function check_xxx(env) -> {"status", "detail", "action"}.
`env` (see build_env) carries every path, URL, the command runner, the HTTP
getter and the clock, so the tests swap in fakes. To add a check: write the
function and add one line to CHECKS. See docs/health.md.
"""
import argparse
import glob
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from types import SimpleNamespace

DOCS = "https://amsterdam-humanities-labs.github.io/signlab_docs/"
PROJECT_FOLDER = "AIHR-FGW-TEST-SIGNLAB (Projectfolder)"
DEFAULT_SERVER_URL = "https://signcollect.nl"
SEVERITY = {"ok": 0, "unknown": 1, "warn": 1, "fail": 2}

# Thresholds. docs/health.md lists them; keep the two in step.
CACHE_WARN_PCT, CACHE_FAIL_PCT = 15, 5          # % free on the external disk
RCLONE_CACHE_WARN_GB, RCLONE_CACHE_FAIL_GB = 100, 20  # room for rclone's write cache
BATCH_SILENT_HOURS = 2      # batch sleeps 1 h between runs; longer silence is a hang
BATCH_RUNNING_HOURS = 12    # one batch run taking longer than this is probably stuck
UPLOAD_WARN_DAYS, UPLOAD_FAIL_DAYS = 1, 3
LOG_TAIL_BYTES = 4 * 1024 * 1024   # one batch run logs ~0.5 MB; never read a whole log

# Services that startupScript.py supervises: (name, text in its command line
# relative to the pipeline dir, breaks the pipeline when down, log file,
# minutes of log silence that means "hung" or None when it is quiet by design).
SERVICES = [
    ("rclone", "rclone mount", True, "rclone.log", 15),
    ("moveFiles", "services/moveFiles.py", True, "moveFiles.log", 60),
    ("batch", "services/batch_queue.py", True, "batch.log", None),
    ("crop", "services/crop.py", True, "crop.log", 180),
    ("convertFiles", "services/convertFiles.py", True, "convertFiles.log", 180),
    ("listFiles", "services/listFiles.py", False, "listFiles.log", None),
    ("networkManager", "services/network_manager.py", False, "networkManager.log", 15),
    ("qrScanner", "qr/qr_scanner_service.py", False, "qrScanner.log", None),
    ("mouse", "services/mouse.py", False, "mouse.log", None),
    ("keyboardMonitor", "services/keyboard_monitor.py", False, "keyboardMonitor.log", None),
    ("watchdog", "scripts/watchdog.sh", False, None, None),
]


def result(status, detail, action=""):
    return {"status": status, "detail": detail, "action": action}


# ----------------------------------------------------------------- helpers

def run_command(cmd, timeout=5):
    """Run cmd, return (returncode, stdout). Raises on timeout or missing binary."""
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, errors="replace")
    return p.returncode, p.stdout


def http_get(url, headers=None, timeout=4):
    """GET url, return (status, body). An HTTP error status is returned, not raised."""
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(65536).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""


def mac_name():
    """The Mac's own name. gethostname() gives the network's name for it on
    Wi-Fi (re-byodm-145-109-...), which changes and tells nobody anything."""
    try:
        out = subprocess.run(["/usr/sbin/scutil", "--get", "LocalHostName"], capture_output=True,
                             text=True, timeout=3).stdout.strip()
        if out:
            return out
    except (OSError, subprocess.SubprocessError):
        pass
    return socket.gethostname().split(".")[0]


def tail(path, max_bytes=LOG_TAIL_BYTES):
    """The last max_bytes of a local log, as text. crop.log is 500 MB: never read it all."""
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        fh.seek(max(0, fh.tell() - max_bytes))
        return fh.read().decode("utf-8", "replace")


def processes(env):
    """[(pid, command line)] of everything running."""
    _, out = env.run(["ps", "-axww", "-o", "pid=,command="], timeout=5)
    procs = []
    for line in out.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            procs.append((int(parts[0]), parts[1]))
    return procs


def is_mounted(env, path):
    _, out = env.run(["mount"], timeout=5)
    return f" on {path} (" in out


def disk_free(env, path):
    """(free bytes, total bytes) from df. In a subprocess: statvfs on a hung mount never returns."""
    _, out = env.run(["df", "-k", path], timeout=5)
    m = re.search(r"\s(\d+)\s+(\d+)\s+(\d+)\s+\d+%", out)
    if not m:
        raise ValueError(f"cannot read df output for {path}")
    return int(m.group(3)) * 1024, int(m.group(1)) * 1024


def human(nbytes):
    for unit, size in (("TB", 1024 ** 4), ("GB", 1024 ** 3), ("MB", 1024 ** 2)):
        if nbytes >= size:
            return f"{nbytes / size:.1f} {unit}"
    return f"{nbytes} bytes"


def age_text(seconds):
    if seconds >= 2 * 86400:
        return f"{seconds / 86400:.0f} days"
    if seconds >= 2 * 3600:
        return f"{seconds / 3600:.0f} h"
    return f"{seconds / 60:.0f} min"


def why(e):
    """Short reason of a network error, without urllib's <urlopen error ...> wrapper."""
    return str(getattr(e, "reason", None) or e)


def host_of(url):
    return re.sub(r"^https?://", "", url).split("/")[0]


# ------------------------------------------------------------------ checks

def check_research_drive(env):
    restart = "See the help page; the start-up job remounts the drive by itself once the network is back."
    rclone = [cmd for _, cmd in processes(env) if "rclone mount" in cmd and env.mount_path in cmd]
    if not is_mounted(env, env.mount_path):
        why = "rclone is running but has not mounted it yet" if rclone else "rclone is not running"
        return result("fail", f"not mounted ({why})", restart)
    # /bin/test in a child with a timeout: a wedged FUSE mount blocks stat() forever.
    try:
        rc, _ = env.run(["/bin/test", "-r", env.mount_marker], timeout=5)
    except subprocess.TimeoutExpired:
        return result("fail", "mounted but not responding", restart)
    if rc != 0:
        return result("fail", "mounted, but the marker file do_not_remove_for_rclone is not readable", restart)
    if not rclone:
        return result("fail", "mounted, but the rclone process is gone", restart)

    # The mount itself reports 0 bytes free even when healthy (the WebDAV quota
    # is not passed on), so "free space" is the disk rclone buffers writes on.
    m = re.search(r"--cache-dir[= ](\S+)", rclone[0])
    cache_dir = m.group(1) if m else env.rclone_cache_dir
    free, _ = disk_free(env, cache_dir)
    detail = f"mounted, {human(free)} free on its cache disk"
    backlog = rclone_backlog(env)
    if backlog:
        detail += f", {backlog} files still syncing"
    free_gb = free / 1024 ** 3
    if free_gb < RCLONE_CACHE_FAIL_GB:
        return result("fail", detail, f"Free up space on {cache_dir}: new recordings cannot be saved.")
    if free_gb < RCLONE_CACHE_WARN_GB:
        return result("warn", detail, f"Free up space on {cache_dir} soon.")
    return result("ok", detail)


def rclone_backlog(env):
    """Files rclone still has to send to the research drive, from its last log line. 0 if unknown."""
    try:
        text = tail(os.path.join(env.logs_dir, "rclone.log"), 65536)
    except OSError:
        return 0
    found = re.findall(r"to upload (\d+), uploading (\d+)", text)
    return int(found[-1][0]) + int(found[-1][1]) if found else 0


def check_cache_disk(env):
    if not is_mounted(env, env.cache_disk):
        return result("fail", f"{env.cache_disk} is not mounted",
                      "Check the cable and power of the external disk, then see the help page.")
    free, total = disk_free(env, env.cache_disk)
    pct = 100.0 * free / total if total else 0.0
    detail = f"mounted, {human(free)} free of {human(total)} ({pct:.0f}%)"
    if pct < CACHE_FAIL_PCT:
        return result("fail", detail, "The disk is almost full: free up space before recording.")
    if pct < CACHE_WARN_PCT:
        return result("warn", detail, "The disk is filling up: ask a sysadmin to free up space.")
    return result("ok", detail)


BATCH_START = re.compile(r"=== Batch Queue Script Started at (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) ===")
# The run stopped because Resolve (or its project) did not work.
BATCH_FAIL = re.compile(r"Failed to open DaVinci Resolve|Failed to load project|Failed to queue files"
                        r"|Failed to add any files to media pool|Batch \S+ failed\. Stopping|Unexpected error:")
# Single files that went wrong; the batch retries them next hour.
BATCH_FILE_PROBLEM = re.compile(r"Failed to copy|Failed to move|Failed to create timeline"
                                r"|Failed to import Fusion comp|Failed to add \S+ to render queue"
                                r"|Failed to find \S+ in media pool|No files copied successfully")
BATCH_BLOCKED = re.compile(r"Mount is not healthy|Pipeline down:")
BATCH_END = re.compile(r"Sleeping for 1 hour|Script Completed|Pipeline down:")


def judge_batch_run(text):
    """(status, summary, finished) for the log lines of one batch run."""
    finished = bool(BATCH_END.search(text))
    m = BATCH_FAIL.search(text)
    if m:
        line = text[m.start():].splitlines()[0].strip()
        return "fail", f"failed: {line[:90]}", finished
    if BATCH_BLOCKED.search(text):
        return "warn", "skipped: the research drive was not available", finished
    rendered = len(re.findall(r"Moved \S+ to post_noncropped", text))
    problems = len(BATCH_FILE_PROBLEM.findall(text))
    if problems:
        return "warn", f"{rendered} rendered, {problems} file problems", finished
    return "ok", (f"ok, {rendered} rendered" if rendered else "ok, nothing to render"), finished


def check_resolve(env):
    """Judged from the batch service's log, not from the Resolve process: the
    batch starts and kills Resolve every hour, so "not running" is normal."""
    if not os.path.isdir(env.resolve_app):
        return result("fail", "DaVinci Resolve is not installed",
                      "Install DaVinci Resolve Studio; see the help page.")
    studio = bool(glob.glob(os.path.join(env.resolve_license_dir, "*studio*"))
                  + glob.glob(os.path.join(env.resolve_license_dir, ".*studio*")))
    log = os.path.join(env.logs_dir, "batch.log")
    if not os.path.isfile(log):
        return result("unknown", "installed, but there is no batch log yet",
                      "Check that the pipeline is running.")
    text = tail(log)
    starts = list(BATCH_START.finditer(text))
    if not starts:
        return result("unknown", "installed, but no batch run found in the recent log",
                      "Check that the pipeline is running.")
    runs = [(m.group(1), text[m.start():(starts[i + 1].start() if i + 1 < len(starts) else len(text))])
            for i, m in enumerate(starts)]
    now = env.now().replace(tzinfo=None)
    started, body = runs[-1]
    status, summary, finished = judge_batch_run(body)
    hhmm = started[11:16]
    action = "Read the last lines of logs/batch.log; see the help page."

    if not finished:
        running_h = (now - datetime.strptime(started, "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600
        waiting = re.findall(r"\[batch_queue\] (?:Still waiting|Waiting): (.*)", body)
        if waiting and "restored after" not in body:
            return result("warn", f"batch of {hhmm} is waiting: {waiting[-1][:80]}",
                          "Check the Research drive and Network lines.")
        if status == "ok" and running_h > BATCH_RUNNING_HOURS:
            return result("warn", f"batch has been running since {started[:16]}", action)
        if status == "ok":
            # Say what THIS run is doing: its own queue and what it has finished.
            queued = len(re.findall(r"Added \S+ to render queue", body))
            done = len(re.findall(r"Moved \S+ to post_noncropped", body))
            if queued:
                detail = f"rendering since {hhmm}: {done} of {queued} clips done"
            elif "Rendering in progress" in body:
                detail = f"rendering since {hhmm}"
            else:
                detail = f"batch started at {hhmm}, preparing"
            if len(runs) > 1:
                # The last finished run decides the colour while this one has no errors.
                prev_started, prev_body = runs[-2]
                status, summary, _ = judge_batch_run(prev_body)
                if status != "ok":
                    detail += f"; batch of {prev_started[11:16]} {summary}"
        else:
            detail = f"batch running since {hhmm}, {summary}"
    else:
        detail = f"last batch {hhmm} {summary}"
        silent_h = (env.now().timestamp() - os.path.getmtime(log)) / 3600
        if status == "ok" and silent_h > BATCH_SILENT_HOURS:
            return result("warn", f"no batch has started since {started[:16]}",
                          "Check the Pipeline line: the batch service may be hung.")
    if status == "ok" and not studio:
        return result("warn", detail + " (no Studio licence found)",
                      "Check that the Studio edition is installed and activated.")
    return result(status, detail, "" if status == "ok" else action)


def check_pipeline(env):
    procs = processes(env)
    start = f"Start it: cd {env.pipeline_dir} && /usr/bin/python3 startupScript.py (see the help page)."
    try:
        with open(os.path.join(env.pipeline_dir, "startup.pid")) as fh:
            pid = int(fh.read().strip())
    except (OSError, ValueError):
        pid = None
    if not any(p == pid and "startupScript.py" in cmd for p, cmd in procs):
        return result("fail", "the start-up job (startupScript.py) is not running", start)

    down_core, down_helper, stale = [], [], []
    now = env.now().timestamp()
    for name, needle, core, log, max_minutes in SERVICES:
        if needle.endswith((".py", ".sh")):
            needle = os.path.join(env.pipeline_dir, needle)
        if not any(needle in cmd for _, cmd in procs):
            (down_core if core else down_helper).append(name)
            continue
        if max_minutes is None:
            continue
        try:
            silent = now - os.path.getmtime(os.path.join(env.logs_dir, log))
        except OSError:
            continue
        if silent > max_minutes * 60:
            stale.append(f"{name} ({age_text(silent)})")

    down = down_core + down_helper
    if down:
        detail = f"{len(SERVICES) - len(down)} of {len(SERVICES)} services alive; not running: {', '.join(down)}"
        # The start-up job restarts a crashed service within a minute and pauses
        # it for 15 minutes after 5 crashes, so a helper being down is a warning.
        return result("fail" if down_core else "warn", detail,
                      "Wait a minute and check again; if it stays down read startup.log and the service log.")
    if stale:
        return result("warn", f"all services alive, but no log output from {', '.join(stale)}",
                      "The service may be hung: read its log in logs/.")
    return result("ok", f"start-up job running, {len(SERVICES)} of {len(SERVICES)} services alive")


def expected_cameras(env):
    """--cameras wins, then expected_cameras in the controller app's config.json."""
    if env.expected_cameras:
        return env.expected_cameras
    for path in env.controller_configs:
        try:
            with open(path) as fh:
                n = int(json.load(fh).get("expected_cameras") or 0)
        except (OSError, ValueError, TypeError, AttributeError):
            continue
        if n > 0:
            return n
    return None


def check_cameras(env):
    url = env.camera_url.rstrip("/") + "/api/status"
    reset = "Check power and USB of the cameras, then use Scan in the controller app."
    try:
        status = {}
        for _ in range(2):
            # The server answers with an empty list while it is busy; ask once more.
            code, body = env.http_get(url, timeout=3)
            if code != 200:
                return result("fail", f"camera server answered HTTP {code}", "Restart the controller app.")
            status = json.loads(body)
            if status.get("cameras"):
                break
    except (OSError, ValueError) as e:
        return result("fail", f"camera server does not answer on {host_of(env.camera_url)} ({why(e)})"[:140],
                      "Start the controller app (it starts the camera server).")
    cams = status.get("cameras") or []
    connected = sum(1 for c in cams if c.get("connected"))
    expected = expected_cameras(env) or len(cams)
    detail = f"{connected} of {expected} connected"
    # The server disconnects the cameras on purpose while it copies or lists files.
    busy = [word for key, word in (("downloading", "downloading files"), ("listing", "listing files"),
                                   ("scanning", "scanning")) if status.get(key)]
    if connected >= expected and expected > 0:
        return result("ok", detail)
    if busy:
        return result("ok" if busy[0] != "scanning" else "warn", f"{detail} ({busy[0]})",
                      "" if busy[0] != "scanning" else "Wait for the scan to finish.")
    if connected == 0:
        # Outside a recording session the cameras are simply switched off.
        return result("warn", (detail if expected else "no cameras found") + " (switched off?)", reset)
    return result("warn", detail, reset)


def check_qr_screen(env):
    """The controller app opens the QR page in Chrome with its own profile
    directory and tests for it with `pgrep -f <profile>`; this does the same."""
    for _, cmd in processes(env):
        if env.kiosk_profile in cmd:
            m = re.search(r"--app=(\S+)", cmd)
            return result("ok", "QR page is open" + (f" ({m.group(1).split('/')[-1]})" if m else ""))
    return result("warn", "QR page is not open",
                  "Open it with the QR screen button in the controller app.")


def active_interfaces(env):
    """(wired, wifi): names of the hardware ports that have a link and an IP address."""
    _, ports = env.run(["networksetup", "-listallhardwareports"], timeout=5)
    _, ifconfig = env.run(["ifconfig", "-a"], timeout=5)
    live = set()
    for block in re.split(r"\n(?=\S)", ifconfig):
        if "status: active" in block and re.search(r"\binet \d", block):
            live.add(block.split(":", 1)[0])
    wired, wifi = [], []
    for name, dev in re.findall(r"Hardware Port: (.*)\nDevice: (\S+)", ports):
        if dev not in live or "Bridge" in name or "Bluetooth" in name:
            continue
        (wifi if "Wi-Fi" in name else wired).append(dev)
    return wired, wifi


def tailscale_connected(env):
    """True / False, or None when there is no Tailscale binary. Same test as network_manager.py."""
    for cmd in env.tailscale_cmds:
        try:
            rc, out = env.run([cmd, "status", "--json"], timeout=5)
        except FileNotFoundError:
            continue
        if rc != 0:
            return False
        try:
            data = json.loads(out)
        except ValueError:
            return False
        return data.get("BackendState") == "Running" and bool((data.get("Self") or {}).get("Online"))
    return None


def check_network(env):
    wired, wifi = active_interfaces(env)
    host = host_of(env.server_url)
    try:
        code, _ = env.http_get(env.server_url, timeout=4)
        server_ok = code < 500
        server = f"{host} answers" if server_ok else f"{host} answered HTTP {code}"
    except OSError as e:
        server_ok, server = False, f"{host} does not answer ({why(e)})"[:90]
    ts = tailscale_connected(env)
    link = "ethernet up" if wired else ("on Wi-Fi, ethernet not connected" if wifi else "no network connection")
    ts_text = {True: "Tailscale connected", False: "Tailscale not connected", None: "Tailscale not installed"}[ts]
    detail = f"{link}, {ts_text}, {server}"
    if not wired and not wifi:
        return result("fail", detail, "Check the network cable or Wi-Fi.")
    if not server_ok:
        return result("fail", detail, "The SignCollect server is unreachable: uploads will wait. Tell a sysadmin.")
    if not ts:
        return result("warn", detail, "Open the Tailscale app and connect: sysadmins cannot reach this Mac.")
    if not wired and env.require_ethernet:
        return result("warn", detail, "Plug in the ethernet cable.")
    return result("ok", detail)


# "Expecting value" is not an upload error: upload2.php answers in plain text and
# older checkouts read that as JSON. The file did arrive (see shared/upload_reply.py).
UPLOAD_ERROR = re.compile(r"^(?:Upload failed|Failed to upload|Thumbnail upload failed|Upload error)(?!.*Expecting value).*$", re.M)


def check_uploads(env):
    """convertFiles.py and crop.py leave <video>.upload_failed next to a file
    whose upload failed and retry it every cycle (content "uploaded" = done).
    Only the folders of the last days are listed, in one `find` with a timeout:
    never a scan of the whole mount."""
    if not is_mounted(env, env.mount_path):
        return result("unknown", "research drive is not mounted, cannot look for waiting uploads",
                      "Fix the Research drive line first.")
    today = env.now().replace(tzinfo=None)
    dirs = []
    for back in range(env.upload_days):
        day = (today - timedelta(days=back)).strftime("%Y-%m-%d")
        dirs += [os.path.join(env.studio_files, day, sub) for sub in ("post", "converted")]
    try:
        # grep -L prints the markers that do NOT hold the line "uploaded".
        _, out = env.run(["/usr/bin/find"] + dirs + ["-maxdepth", "1", "-name", "*.upload_failed",
                         "-exec", "/usr/bin/grep", "-L", "-x", "uploaded", "{}", "+"], timeout=8)
    except subprocess.TimeoutExpired:
        return result("unknown", "the research drive was too slow to list the upload markers",
                      "Check the Research drive line and try again.")
    # A marker is rewritten on every retry, so its age is the recording day in its path.
    oldest, waiting = 0.0, 0
    for line in out.splitlines():
        m = re.search(r"/(\d{4}-\d\d-\d\d)/(?:post|converted)/[^/]+\.upload_failed$", line.strip())
        if not m:
            continue
        waiting += 1
        end_of_day = datetime.strptime(m.group(1), "%Y-%m-%d") + timedelta(days=1)
        oldest = max(oldest, (today - end_of_day).total_seconds() / 86400)

    # Uploads the server refused leave no marker, only a line in the service log.
    # The logs carry no timestamps: these tails are roughly the last hours.
    errors = 0
    for log, size in (("convertFiles.log", 256 * 1024), ("crop.log", 2 * 1024 * 1024)):
        try:
            errors += len(UPLOAD_ERROR.findall(tail(os.path.join(env.logs_dir, log), size)))
        except OSError:
            pass

    action = "Check the Network line; if the server is up, read logs/crop.log and logs/convertFiles.log."
    if waiting and oldest > UPLOAD_WARN_DAYS:
        detail = f"{waiting} files waiting, the oldest recorded {int(oldest) + 1} days ago"
        return result("fail" if oldest > UPLOAD_FAIL_DAYS else "warn", detail, action)
    if errors:
        return result("warn", f"{errors} upload errors in the recent service logs"
                      + (f", {waiting} files waiting" if waiting else ""), action)
    if waiting:
        return result("ok", f"{waiting} files waiting since today or yesterday, retried every 15 min")
    return result("ok", f"nothing waiting (last {env.upload_days} days)")


def check_crop_fixes(env):
    if not env.videofix_token:
        return result("warn", "no crop-fix token configured",
                      f"Set VIDEOFIX_TOKEN in {os.path.join(env.pipeline_dir, '.env')} (same value as on the server).")
    url = env.server_url.rstrip("/") + "/videoFix/crop_fixes.json"
    try:
        code, _ = env.http_get(url, headers={"X-Api-Token": env.videofix_token}, timeout=4)
    except OSError as e:
        return result("unknown", f"could not reach {host_of(env.server_url)} ({why(e)})"[:120],
                      "Check the Network line.")
    if code == 200:
        return result("ok", "the server accepts the crop-fix token")
    if code in (401, 403):
        return result("fail", f"the server rejects the crop-fix token (HTTP {code})",
                      "Make VIDEOFIX_TOKEN on this Mac equal to the one on the server.")
    return result("warn", f"the server answered HTTP {code} for the crop-fix queue", "Tell a sysadmin.")


# The registry: id, title, docs anchor, function. Order is the display order.
CHECKS = [
    ("research_drive", "Research drive", "studio/troubleshooting/#studio-research-drive", check_research_drive),
    ("cache_disk", "External disk", "studio/troubleshooting/#studio-cachedisk", check_cache_disk),
    ("resolve", "DaVinci Resolve", "studio/troubleshooting/#studio-resolve", check_resolve),
    ("pipeline", "Pipeline", "studio/troubleshooting/#studio-pipeline-start", check_pipeline),
    ("cameras", "Cameras", "studio/troubleshooting-fx30/", check_cameras),
    ("qr_screen", "QR screen", "studio/troubleshooting/#studio-qr-screen", check_qr_screen),
    ("network", "Network", "studio/troubleshooting/#studio-camera-server", check_network),
    ("uploads", "Uploads", "studio/troubleshooting/#studio-no-upload", check_uploads),
    ("crop_fixes", "Crop fixes", "troubleshooting/video-processing/#vp-crop-token", check_crop_fixes),
]


# ------------------------------------------------------------------ runner

def run_check(func, env):
    """A check never raises: any error becomes status "unknown"."""
    try:
        res = func(env)
        if res.get("status") not in SEVERITY:
            raise ValueError(f"bad status {res.get('status')!r}")
        return result(res["status"], str(res.get("detail", "")), str(res.get("action", "")))
    except Exception as e:
        return result("unknown", f"check could not run: {type(e).__name__}: {e}"[:160],
                      "Run tools/health.py again; if this stays, tell a sysadmin.")


def run_checks(env, checks=CHECKS, deadline=10):
    """Run the checks side by side (the slow ones wait on the network) and
    build the report. A check still busy at the deadline is reported unknown."""
    results = {}

    def work(check_id, func):
        results[check_id] = run_check(func, env)

    threads = [threading.Thread(target=work, args=(c[0], c[3]), daemon=True) for c in checks]
    for t in threads:
        t.start()
    end = datetime.now().timestamp() + deadline
    for t in threads:
        t.join(max(0.0, end - datetime.now().timestamp()))

    rows = []
    for check_id, title, anchor, _ in checks:
        res = results.get(check_id) or result("unknown", f"check did not finish in {deadline} s",
                                              "Run tools/health.py again.")
        rows.append({"id": check_id, "title": title, "status": res["status"], "detail": res["detail"],
                     "action": res["action"], "help_url": DOCS + anchor})
    worst = max([SEVERITY[r["status"]] for r in rows] or [0])
    return {"generated_at": env.now().isoformat(timespec="seconds"), "host": env.host,
            "overall": ("ok", "warn", "fail")[worst], "checks": rows}


def format_text(report):
    labels = {"ok": "OK", "warn": "WARN", "fail": "FAIL", "unknown": "UNKNOWN"}
    width = max([len(c["title"]) for c in report["checks"]] or [0])
    lines = [f"Studio health on {report['host']} at {report['generated_at'][:16].replace('T', ' ')}: "
             f"{labels[report['overall']]}", ""]
    for c in report["checks"]:
        lines.append(f"{labels[c['status']]:<7}  {c['title']:<{width}}  {c['detail']}")
    todo = [c for c in report["checks"] if c["action"]]
    if todo:
        lines += ["", "What to do:"]
        for c in todo:
            lines += [f"- {c['title']}: {c['action']}", f"  {c['help_url']}"]
    return "\n".join(lines)


# ------------------------------------------------------------------- setup

def server_settings(pipeline_dir, environ):
    """(server URL, videoFix token). Uses shared/server_config.py when the
    checkout has it; the DRS still runs an older tree without it and without .env."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for root in (here, pipeline_dir):
        path = os.path.join(root, "shared", "server_config.py")
        if not os.path.isfile(path):
            continue
        try:
            spec = importlib.util.spec_from_file_location("_health_server_config", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod.SERVER_URL, mod.VIDEOFIX_TOKEN
        except Exception:
            break
    values = {}
    try:
        with open(os.path.join(pipeline_dir, ".env")) as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    values[key.strip()] = value.strip().strip("\"'")
    except OSError:
        pass
    url = environ.get("SIGNCOLLECT_URL") or values.get("SIGNCOLLECT_URL") or DEFAULT_SERVER_URL
    return url.rstrip("/"), environ.get("VIDEOFIX_TOKEN") or values.get("VIDEOFIX_TOKEN", "")


def build_env(args=None, environ=None):
    """Everything the checks touch, with the DRS Mac's real paths as defaults.
    Options win over DRS_HEALTH_* environment variables, which win over defaults."""
    args = args or SimpleNamespace()
    environ = os.environ if environ is None else environ

    def pick(name, default):
        return getattr(args, name, None) or environ.get("DRS_HEALTH_" + name.upper()) or default

    home = environ.get("DRS_HEALTH_HOME") or "/Users/signlab"
    pipeline_dir = pick("pipeline_dir", os.path.join(home, "drs"))
    mount_path = pick("mount", os.path.join(home, "signCollect"))
    server_url, token = server_settings(pipeline_dir, environ)
    return SimpleNamespace(
        pipeline_dir=pipeline_dir,
        logs_dir=os.path.join(pipeline_dir, "logs"),
        mount_path=mount_path,
        mount_marker=os.path.join(mount_path, PROJECT_FOLDER, "do_not_remove_for_rclone"),
        studio_files=os.path.join(mount_path, PROJECT_FOLDER, "studioFiles"),
        cache_disk=pick("cache_disk", "/Volumes/cacheDisk"),
        rclone_cache_dir="/Volumes/cacheDisk/rclone",
        resolve_app="/Applications/DaVinci Resolve/DaVinci Resolve.app",
        resolve_license_dir="/Library/Application Support/Blackmagic Design/DaVinci Resolve/.license",
        camera_url=pick("camera_url", "http://127.0.0.1:8080"),
        expected_cameras=int(pick("cameras", 0) or 0),
        # The DRS has the SDK as ~/Sony-SDK-MACOS-API; a fresh setup clones it under the repo name.
        controller_configs=[os.path.join(home, d, "pyqtController", "config.json")
                            for d in ("Sony-SDK-MACOS-API", "signlab_Sony-SDK-MACOS-API")],
        kiosk_profile=os.path.join(home, ".signcollect-kiosk-chrome"),
        server_url=(pick("server_url", server_url)).rstrip("/"),
        videofix_token=token,
        tailscale_cmds=["/Applications/Tailscale.app/Contents/MacOS/Tailscale",
                        "/opt/homebrew/bin/tailscale", "/usr/local/bin/tailscale"],
        require_ethernet=bool(getattr(args, "require_ethernet", False)),
        upload_days=int(pick("upload_days", 30)),
        host=mac_name(),
        now=lambda: datetime.now().astimezone(),
        run=run_command,
        http_get=http_get,
    )


def main(argv=None, env=None):
    parser = argparse.ArgumentParser(description="Studio health check for the DRS Mac (read-only).")
    parser.add_argument("--json", action="store_true", help="print one JSON object and exit 0")
    parser.add_argument("--only", metavar="ID", action="append", help="run only this check (repeatable)")
    parser.add_argument("--cameras", type=int, metavar="N", help="number of cameras that should be connected")
    parser.add_argument("--require-ethernet", action="store_true",
                        help="warn when the Mac is on Wi-Fi only (the studio normally is)")
    parser.add_argument("--upload-days", type=int, metavar="N", help="recording days to look back for waiting uploads (30)")
    parser.add_argument("--pipeline-dir", help="pipeline checkout (/Users/signlab/drs)")
    parser.add_argument("--mount", help="research drive mount point (/Users/signlab/signCollect)")
    parser.add_argument("--cache-disk", help="external disk (/Volumes/cacheDisk)")
    parser.add_argument("--camera-url", help="camera server (http://127.0.0.1:8080)")
    parser.add_argument("--server-url", help="SignCollect server (SIGNCOLLECT_URL, else https://signcollect.nl)")
    args = parser.parse_args(argv)

    checks = CHECKS
    if args.only:
        unknown_ids = set(args.only) - {c[0] for c in CHECKS}
        if unknown_ids:
            parser.error(f"unknown check: {', '.join(sorted(unknown_ids))} "
                         f"(choose from {', '.join(c[0] for c in CHECKS)})")
        checks = [c for c in CHECKS if c[0] in args.only]

    report = run_checks(env or build_env(args), checks)
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    print(format_text(report))
    return SEVERITY[report["overall"]]


if __name__ == "__main__":
    sys.exit(main())
