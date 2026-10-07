"""tools/health.py: every check against fakes (tmp_path folders, canned command
output, canned HTTP answers), the roll-up, the JSON shape and the exit codes."""
import importlib.util
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("health", os.path.join(ROOT, "tools", "health.py"))
health = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(health)

NOW = datetime(2026, 10, 7, 14, 2, 11, tzinfo=timezone(timedelta(hours=2)))
GB = 1024 * 1024  # 1 GB in the 1 KB blocks that df -k prints

PORTS = ("\nHardware Port: Ethernet\nDevice: en0\nEthernet Address: aa\n"
         "\nHardware Port: Thunderbolt Bridge\nDevice: bridge0\nEthernet Address: bb\n"
         "\nHardware Port: Wi-Fi\nDevice: en1\nEthernet Address: cc\n")


def ifconfig(*active):
    out = ""
    for dev in ("lo0", "en0", "en1", "bridge0"):
        up = dev in active
        out += f"{dev}: flags=8863<UP> mtu 1500\n"
        out += "\tinet 10.0.0.2 netmask 0xffffff00\n" if up else ""
        out += f"\tstatus: {'active' if up else 'inactive'}\n"
    return out


def df(free_kb, total_kb, mounted_on="/x"):
    return ("Filesystem 1024-blocks Used Available Capacity iused ifree %iused  Mounted on\n"
            f"/dev/disk7s1 {total_kb} {total_kb - free_kb} {free_kb} 73% 1 2 0% {mounted_on}\n")


class Fakes:
    """A healthy studio. Tests change one thing and look at one check."""

    def __init__(self, tmp_path):
        self.dir = tmp_path / "drs"
        self.logs = self.dir / "logs"
        self.logs.mkdir(parents=True)
        self.mount = str(tmp_path / "signCollect")
        self.cache = str(tmp_path / "cacheDisk")
        self.resolve_app = tmp_path / "DaVinci Resolve.app"
        self.resolve_app.mkdir()
        self.license = tmp_path / "license"
        self.license.mkdir()
        (self.license / ".davinciresolvestudio_14.0.lic").write_text("x")
        self.kiosk = str(tmp_path / ".signcollect-kiosk-chrome")
        self.controller_config = tmp_path / "config.json"

        (self.dir / "startup.pid").write_text("100\n")
        self.procs = {100: f"Python {self.dir}/startupScript.py",
                      300: f"Chrome --user-data-dir={self.kiosk} --app=https://signcollect.nl/opnameLR.html"}
        for i, (name, needle, _core, log, _max) in enumerate(health.SERVICES):
            if name == "rclone":
                cmd = f"/Users/signlab/rclone/rclone mount signcollect: {self.mount} --cache-dir {self.cache}/rclone"
            else:
                cmd = f"python3 {self.dir}/{needle}"
            self.procs[200 + i] = cmd
            if log:
                self.write_log(log, "fine\n")
        self.write_log("rclone.log", "INFO : vfs cache: cleaned: objects 5 (was 5) in use 0, to upload 0, uploading 0\n")
        self.write_log("batch.log", batch_run("2026-10-07 13:31:51", "No files to process (all already processed or skipped)."))

        self.mounted = {self.mount, self.cache}
        self.free_kb, self.total_kb = 1000 * GB, 3600 * GB
        self.marker_rc = 0
        self.find_out = ""
        self.ifconfig = ifconfig("en0")
        self.tailscale = (0, json.dumps({"BackendState": "Running", "Self": {"Online": True}}))
        self.raise_for = {}          # command name -> exception
        self.http = {"http://cam/api/status": (200, cameras_json(5, 5)),
                     "https://server.test": (200, "<html>"),
                     "https://server.test/videoFix/crop_fixes.json": (200, "[]")}
        self.requests = []

        self.env = SimpleNamespace(
            pipeline_dir=str(self.dir), logs_dir=str(self.logs), mount_path=self.mount,
            mount_marker=self.mount + "/P/do_not_remove_for_rclone", studio_files=self.mount + "/P/studioFiles",
            cache_disk=self.cache, rclone_cache_dir=self.cache + "/rclone",
            resolve_app=str(self.resolve_app), resolve_license_dir=str(self.license),
            camera_url="http://cam", expected_cameras=0, controller_configs=[str(self.controller_config)],
            kiosk_profile=self.kiosk, server_url="https://server.test", videofix_token="secret",
            tailscale_cmds=["/missing/tailscale", "tailscale"], require_ethernet=False, upload_days=30,
            host="signlabs-mini", now=lambda: NOW, run=self.run, http_get=self.http_get)

    def write_log(self, name, text, age_minutes=1):
        path = self.logs / name
        path.write_text(text)
        when = NOW.timestamp() - age_minutes * 60
        os.utime(path, (when, when))

    def run(self, cmd, timeout=5):
        name = os.path.basename(cmd[0])
        if name in self.raise_for:
            raise self.raise_for[name]
        if cmd[0] == "/missing/tailscale":
            raise FileNotFoundError(cmd[0])
        if name == "ps":
            return 0, "".join(f"{pid:>5} {c}\n" for pid, c in self.procs.items())
        if name == "mount":
            return 0, "".join(f"dev on {m} (apfs, local)\n" for m in sorted(self.mounted))
        if name == "df":
            return 0, df(self.free_kb, self.total_kb)
        if name == "test":
            return self.marker_rc, ""
        if name == "find":
            self.find_cmd = cmd
            return 0, self.find_out
        if name == "networksetup":
            return 0, PORTS
        if name == "ifconfig":
            return 0, self.ifconfig
        if name == "tailscale":
            return self.tailscale
        raise AssertionError(f"unexpected command {cmd}")

    def http_get(self, url, headers=None, timeout=4):
        self.requests.append((url, headers))
        answer = self.http[url]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def kill(self, needle):
        self.procs = {p: c for p, c in self.procs.items() if needle not in c}


def cameras_json(connected, total, **flags):
    cams = [{"connected": i < connected, "recording": False} for i in range(total)]
    return json.dumps(dict({"cameras": cams, "downloading": False, "listing": False, "scanning": False}, **flags))


def batch_run(started, *lines, end="Sleeping for 1 hour until next check..."):
    body = [f"=== Batch Queue Script Started at {started} ===", "Killing any existing DaVinci Resolve process..."]
    return "\n".join(body + list(lines) + ([end] if end else [])) + "\n"


@pytest.fixture
def f(tmp_path):
    return Fakes(tmp_path)


def status_of(res):
    assert set(res) == {"status", "detail", "action"}
    assert bool(res["action"]) == (res["status"] != "ok"), res
    return res["status"]


# ----------------------------------------------------------- research drive

def test_research_drive_ok(f):
    res = health.check_research_drive(f.env)
    assert status_of(res) == "ok"
    assert res["detail"] == "mounted, 1000.0 GB free on its cache disk"


def test_research_drive_shows_sync_backlog(f):
    f.write_log("rclone.log", "x to upload 0, uploading 0\ny to upload 12, uploading 4\n")
    assert "16 files still syncing" in health.check_research_drive(f.env)["detail"]


def test_research_drive_not_mounted(f):
    f.mounted.discard(f.mount)
    res = health.check_research_drive(f.env)
    assert status_of(res) == "fail" and "rclone is running" in res["detail"]
    f.kill("rclone mount")
    assert "rclone is not running" in health.check_research_drive(f.env)["detail"]


def test_research_drive_marker_missing_hung_or_no_rclone(f):
    f.marker_rc = 1
    res = health.check_research_drive(f.env)
    assert status_of(res) == "fail" and "marker" in res["detail"]
    f.marker_rc = 0
    f.raise_for["test"] = subprocess.TimeoutExpired("test", 5)
    assert "not responding" in health.check_research_drive(f.env)["detail"]
    f.raise_for.clear()
    f.kill("rclone mount")
    res = health.check_research_drive(f.env)
    assert status_of(res) == "fail" and "rclone process is gone" in res["detail"]


@pytest.mark.parametrize("free_gb,expected", [(150, "ok"), (99, "warn"), (19, "fail")])
def test_research_drive_free_space(f, free_gb, expected):
    f.free_kb = free_gb * GB
    assert status_of(health.check_research_drive(f.env)) == expected


def test_research_drive_error_becomes_unknown(f):
    f.raise_for["ps"] = OSError("ps broke")
    res = health.run_check(health.check_research_drive, f.env)
    assert res["status"] == "unknown" and "ps broke" in res["detail"]


# ------------------------------------------------------------ external disk

@pytest.mark.parametrize("pct,expected", [(27, "ok"), (15, "ok"), (14, "warn"), (5, "warn"), (4, "fail")])
def test_cache_disk_free_space(f, pct, expected):
    f.free_kb, f.total_kb = pct * GB, 100 * GB
    res = health.check_cache_disk(f.env)
    assert status_of(res) == expected and f"({pct}%)" in res["detail"]


def test_cache_disk_not_mounted(f):
    f.mounted.discard(f.cache)
    assert status_of(health.check_cache_disk(f.env)) == "fail"


def test_cache_disk_unreadable_df_becomes_unknown(f):
    f.run = lambda cmd, timeout=5: (0, f"dev on {f.cache} (apfs)\n" if cmd[0] == "mount" else "garbage")
    f.env.run = f.run
    assert health.run_check(health.check_cache_disk, f.env)["status"] == "unknown"


# ------------------------------------------------------------------ resolve

def test_resolve_ok_nothing_to_render(f):
    res = health.check_resolve(f.env)
    assert status_of(res) == "ok" and res["detail"] == "last batch 13:31 ok, nothing to render"


def test_resolve_ok_counts_renders(f):
    f.write_log("batch.log", batch_run("2026-10-07 12:00:00", "Batch render complete!",
                                       "  Moved L20261007_0001.MP4 to post_noncropped",
                                       "  Moved R20261007_0001.MP4 to post_noncropped",
                                       "\n=== Batch Queue Script Completed at 2026-10-07 12:40:00 ==="))
    assert health.check_resolve(f.env)["detail"] == "last batch 12:00 ok, 2 rendered"


def test_resolve_not_installed(f):
    f.resolve_app.rmdir()
    assert status_of(health.check_resolve(f.env)) == "fail"


@pytest.mark.parametrize("line", ["Failed to open DaVinci Resolve", "Failed to queue files - project not loaded",
                                  "Batch 2 failed. Stopping.", "Unexpected error: boom"])
def test_resolve_failed_batch(f, line):
    f.write_log("batch.log", batch_run("2026-10-07 13:00:00", line))
    res = health.check_resolve(f.env)
    assert status_of(res) == "fail" and line[:20] in res["detail"]


def test_resolve_ignores_heartbeat_failures(f):
    f.write_log("batch.log", batch_run("2026-10-07 13:00:00", "Failed to send heartbeat for 'DRS Batch Queue': x",
                                       "No files to process (all already processed or skipped)."))
    assert status_of(health.check_resolve(f.env)) == "ok"


def test_resolve_warns_on_file_problems_and_skipped_batch(f):
    f.write_log("batch.log", batch_run("2026-10-07 13:00:00", "  Failed to copy L1.MP4: rsync failed",
                                       "  Moved R1.MP4 to post_noncropped"))
    res = health.check_resolve(f.env)
    assert status_of(res) == "warn" and "1 rendered, 1 file problems" in res["detail"]
    f.write_log("batch.log", batch_run("2026-10-07 13:00:00", "Mount is not healthy. Exiting."))
    assert status_of(health.check_resolve(f.env)) == "warn"


def test_resolve_running_uses_previous_run(f):
    running = batch_run("2026-10-07 13:50:00", "Rendering in progress... (13:55:00)", end=None)
    f.write_log("batch.log", batch_run("2026-10-07 12:00:00", "Failed to open DaVinci Resolve") + running)
    res = health.check_resolve(f.env)
    assert status_of(res) == "fail" and res["detail"].startswith("batch running since 13:50; batch of 12:00 failed")
    f.write_log("batch.log", batch_run("2026-10-07 12:00:00", "No files to process") + running)
    assert status_of(health.check_resolve(f.env)) == "ok"
    f.write_log("batch.log", running)
    assert health.check_resolve(f.env)["detail"] == "batch running since 13:50, ok, nothing to render"


def test_resolve_warns_when_waiting_stuck_or_silent(f):
    f.write_log("batch.log", batch_run("2026-10-07 13:50:00", "[batch_queue] Waiting: DNS lookup failed for x", end=None))
    res = health.check_resolve(f.env)
    assert status_of(res) == "warn" and "DNS lookup failed" in res["detail"]
    f.write_log("batch.log", batch_run("2026-10-06 22:00:00", "Rendering in progress...", end=None))
    assert status_of(health.check_resolve(f.env)) == "warn"
    f.write_log("batch.log", batch_run("2026-10-07 09:00:00", "No files to process"), age_minutes=200)
    res = health.check_resolve(f.env)
    assert status_of(res) == "warn" and "no batch has started since 2026-10-07 09:00" in res["detail"]


def test_resolve_warns_without_studio_licence(f):
    (f.license / ".davinciresolvestudio_14.0.lic").unlink()
    res = health.check_resolve(f.env)
    assert status_of(res) == "warn" and "Studio" in res["detail"]


def test_resolve_unknown_without_log_or_runs(f):
    f.write_log("batch.log", "Skipping L1.MP4 - already in post_noncropped\n")
    assert status_of(health.check_resolve(f.env)) == "unknown"
    (f.logs / "batch.log").unlink()
    assert status_of(health.check_resolve(f.env)) == "unknown"


def test_tail_reads_only_the_end(tmp_path):
    path = tmp_path / "big.log"
    path.write_text("a" * 1000 + "END")
    assert health.tail(str(path), 5) == "aaEND"


# ----------------------------------------------------------------- pipeline

def test_pipeline_ok(f):
    res = health.check_pipeline(f.env)
    assert status_of(res) == "ok" and "11 of 11 services alive" in res["detail"]


def test_pipeline_startup_job_dead_or_pid_reused(f):
    f.procs[100] = "some other program"
    assert status_of(health.check_pipeline(f.env)) == "fail"
    (f.dir / "startup.pid").unlink()
    assert status_of(health.check_pipeline(f.env)) == "fail"


def test_pipeline_core_service_down_fails_helper_warns(f):
    f.kill("services/mouse.py")
    res = health.check_pipeline(f.env)
    assert status_of(res) == "warn" and "10 of 11" in res["detail"] and "mouse" in res["detail"]
    f.kill("services/crop.py")
    res = health.check_pipeline(f.env)
    assert status_of(res) == "fail" and "crop, mouse" in res["detail"]


def test_pipeline_stale_log_warns_only_for_chatty_services(f):
    f.write_log("keyboardMonitor.log", "old\n", age_minutes=60 * 24 * 9)
    f.write_log("batch.log", "old\n", age_minutes=600)
    assert status_of(health.check_pipeline(f.env)) == "ok"
    f.write_log("crop.log", "old\n", age_minutes=300)
    res = health.check_pipeline(f.env)
    assert status_of(res) == "warn" and "crop (5 h)" in res["detail"]


def test_pipeline_unknown_when_ps_fails(f):
    f.raise_for["ps"] = subprocess.TimeoutExpired("ps", 5)
    assert health.run_check(health.check_pipeline, f.env)["status"] == "unknown"


# ------------------------------------------------------------------ cameras

def test_cameras_all_connected(f):
    res = health.check_cameras(f.env)
    assert status_of(res) == "ok" and res["detail"] == "5 of 5 connected"
    assert f.requests == [("http://cam/api/status", None)]


def test_cameras_expected_from_option_or_controller_config(f):
    f.http["http://cam/api/status"] = (200, cameras_json(3, 3))
    assert status_of(health.check_cameras(f.env)) == "ok"       # count from the status itself
    f.controller_config.write_text('{"expected_cameras": 5}')
    res = health.check_cameras(f.env)
    assert status_of(res) == "warn" and res["detail"] == "3 of 5 connected"
    f.env.expected_cameras = 3
    assert status_of(health.check_cameras(f.env)) == "ok"


def test_cameras_none_connected_or_none_found(f):
    f.http["http://cam/api/status"] = (200, cameras_json(0, 5))
    res = health.check_cameras(f.env)
    assert status_of(res) == "warn" and res["detail"] == "0 of 5 connected (switched off?)"
    f.http["http://cam/api/status"] = (200, cameras_json(0, 0))
    res = health.check_cameras(f.env)
    assert status_of(res) == "warn" and res["detail"] == "no cameras found (switched off?)"
    assert len(f.requests) == 3   # an empty list is asked for a second time


def test_cameras_disconnected_on_purpose_while_downloading(f):
    f.http["http://cam/api/status"] = (200, cameras_json(0, 5, downloading=True))
    res = health.check_cameras(f.env)
    assert status_of(res) == "ok" and "downloading files" in res["detail"]
    f.http["http://cam/api/status"] = (200, cameras_json(2, 5, scanning=True))
    assert status_of(health.check_cameras(f.env)) == "warn"


def test_cameras_server_down_or_talking_nonsense(f):
    f.http["http://cam/api/status"] = ConnectionRefusedError("Connection refused")
    res = health.check_cameras(f.env)
    assert status_of(res) == "fail" and "does not answer on cam (Connection refused)" in res["detail"]
    f.http["http://cam/api/status"] = (200, "<html>")
    assert status_of(health.check_cameras(f.env)) == "fail"
    f.http["http://cam/api/status"] = (500, "")
    assert status_of(health.check_cameras(f.env)) == "fail"
    f.http["http://cam/api/status"] = (200, "[1]")   # valid JSON, wrong shape
    assert health.run_check(health.check_cameras, f.env)["status"] == "unknown"


# ---------------------------------------------------------------- QR screen

def test_qr_screen(f):
    res = health.check_qr_screen(f.env)
    assert status_of(res) == "ok" and res["detail"] == "QR page is open (opnameLR.html)"
    f.kill("kiosk-chrome")
    assert status_of(health.check_qr_screen(f.env)) == "warn"
    f.raise_for["ps"] = OSError("no ps")
    assert health.run_check(health.check_qr_screen, f.env)["status"] == "unknown"


# ------------------------------------------------------------------ network

def test_network_ok_on_ethernet(f):
    res = health.check_network(f.env)
    assert status_of(res) == "ok"
    assert res["detail"] == "ethernet up, Tailscale connected, server.test answers"


def test_network_wifi_only_is_ok_unless_ethernet_required(f):
    f.ifconfig = ifconfig("en1", "bridge0")
    res = health.check_network(f.env)
    assert status_of(res) == "ok" and "on Wi-Fi, ethernet not connected" in res["detail"]
    f.env.require_ethernet = True
    assert status_of(health.check_network(f.env)) == "warn"


def test_network_tailscale_down_or_missing_warns(f):
    f.tailscale = (0, json.dumps({"BackendState": "Running", "Self": {"Online": False}}))
    res = health.check_network(f.env)
    assert status_of(res) == "warn" and "Tailscale not connected" in res["detail"]
    f.tailscale = (1, "")
    assert status_of(health.check_network(f.env)) == "warn"
    f.env.tailscale_cmds = ["/missing/tailscale"]
    res = health.check_network(f.env)
    assert status_of(res) == "warn" and "Tailscale not installed" in res["detail"]


def test_network_fails_without_link_or_server(f):
    f.http["https://server.test"] = OSError("timed out")
    res = health.check_network(f.env)
    assert status_of(res) == "fail" and "server.test does not answer (timed out)" in res["detail"]
    f.http["https://server.test"] = (502, "")
    assert status_of(health.check_network(f.env)) == "fail"
    f.http["https://server.test"] = (200, "")
    f.ifconfig = ifconfig()
    res = health.check_network(f.env)
    assert status_of(res) == "fail" and "no network connection" in res["detail"]


def test_network_unknown_when_a_command_hangs(f):
    f.raise_for["ifconfig"] = subprocess.TimeoutExpired("ifconfig", 5)
    assert health.run_check(health.check_network, f.env)["status"] == "unknown"


# ------------------------------------------------------------------ uploads

def marker(f, day, folder="post", name="L1_h264"):
    return f"{f.env.studio_files}/{day}/{folder}/{name}.upload_failed\n"


def test_uploads_nothing_waiting(f):
    res = health.check_uploads(f.env)
    assert status_of(res) == "ok" and res["detail"] == "nothing waiting (last 30 days)"
    # one bounded find over the post/ and converted/ folders of the last 30 days only
    dirs = [a for a in f.find_cmd if a.startswith(f.env.studio_files)]
    assert len(dirs) == 60 and dirs[0].endswith("/2026-10-07/post") and dirs[-1].endswith("/2026-09-08/converted")
    assert f.find_cmd[f.find_cmd.index("-maxdepth") + 1] == "1"


@pytest.mark.parametrize("day,expected", [("2026-10-07", "ok"), ("2026-10-06", "ok"),
                                          ("2026-10-05", "warn"), ("2026-10-04", "warn"), ("2026-10-03", "fail")])
def test_uploads_age_of_waiting_files(f, day, expected):
    f.find_out = marker(f, "2026-10-07", "converted") + marker(f, day) + "find: /x: No such file or directory\n"
    res = health.check_uploads(f.env)
    assert status_of(res) == expected and res["detail"].startswith("2 files waiting")
    if day == "2026-10-05":
        assert res["detail"] == "2 files waiting, the oldest recorded 2 days ago"


def test_uploads_errors_in_service_logs_warn(f):
    f.write_log("convertFiles.log", "Converting: a\nUpload failed for M1.MP4: HTTP 500\n")
    f.write_log("crop.log", "Failed to upload /x/L1_h264.MP4\nUpload successful: ok\n")
    res = health.check_uploads(f.env)
    assert status_of(res) == "warn" and res["detail"] == "2 upload errors in the recent service logs"


def test_uploads_plain_text_reply_is_not_an_error(f):
    # upload2.php answers in text; older checkouts log that as "Expecting value".
    f.write_log("convertFiles.log", "Upload failed for M1.MP4: Expecting value: line 1 column 1 (char 0)\n" * 50)
    assert status_of(health.check_uploads(f.env)) == "ok"


def test_uploads_unknown_when_drive_down_or_slow(f):
    f.raise_for["find"] = subprocess.TimeoutExpired("find", 8)
    assert status_of(health.check_uploads(f.env)) == "unknown"
    f.mounted.discard(f.mount)
    assert status_of(health.check_uploads(f.env)) == "unknown"


# --------------------------------------------------------------- crop fixes

def test_crop_fixes_ok_sends_token(f):
    assert status_of(health.check_crop_fixes(f.env)) == "ok"
    assert f.requests == [("https://server.test/videoFix/crop_fixes.json", {"X-Api-Token": "secret"})]


def test_crop_fixes_no_token_warns_without_calling_the_server(f):
    f.env.videofix_token = ""
    assert status_of(health.check_crop_fixes(f.env)) == "warn"
    assert f.requests == []


@pytest.mark.parametrize("answer,expected", [((401, ""), "fail"), ((403, ""), "fail"), ((500, ""), "warn"),
                                             (OSError("timed out"), "unknown")])
def test_crop_fixes_bad_answers(f, answer, expected):
    f.http["https://server.test/videoFix/crop_fixes.json"] = answer
    assert status_of(health.check_crop_fixes(f.env)) == expected


# ------------------------------------------------- roll-up, JSON, exit codes

def fake_checks(*statuses):
    return [(f"c{i}", f"Check {i}", f"page/#c{i}", (lambda env, s=s: health.result(s, f"is {s}", "" if s == "ok" else "fix it")))
            for i, s in enumerate(statuses)]


@pytest.mark.parametrize("statuses,overall", [(("ok", "ok"), "ok"), (("ok", "warn"), "warn"),
                                              (("ok", "unknown"), "warn"), (("warn", "fail", "unknown"), "fail"),
                                              ((), "ok")])
def test_overall_is_the_worst_status(f, statuses, overall):
    assert health.run_checks(f.env, fake_checks(*statuses))["overall"] == overall


def test_report_shape_of_a_healthy_studio(f):
    report = health.run_checks(f.env)
    assert list(report) == ["generated_at", "host", "overall", "checks"]
    assert report["generated_at"] == "2026-10-07T14:02:11+02:00" and report["host"] == "signlabs-mini"
    assert [c["id"] for c in report["checks"]] == ["research_drive", "cache_disk", "resolve", "pipeline", "cameras",
                                                   "qr_screen", "network", "uploads", "crop_fixes"]
    for c in report["checks"]:
        assert list(c) == ["id", "title", "status", "detail", "action", "help_url"]
        assert c["status"] == "ok" and c["action"] == "" and c["detail"], c
        assert c["help_url"].startswith("https://amsterdam-humanities-labs.github.io/signlab_docs/")
    assert report["overall"] == "ok"
    assert report["checks"][0]["help_url"].endswith("studio/troubleshooting/#studio-research-drive")
    assert report["checks"][8]["help_url"].endswith("troubleshooting/video-processing/#vp-crop-token")


def test_a_check_that_raises_or_misbehaves_becomes_unknown(f):
    def boom(env):
        raise RuntimeError("kaboom")
    checks = [("a", "A", "x", boom), ("b", "B", "x", lambda env: {"status": "purple"}),
              ("c", "C", "x", lambda env: None)]
    report = health.run_checks(f.env, checks)
    assert [c["status"] for c in report["checks"]] == ["unknown"] * 3
    assert "RuntimeError: kaboom" in report["checks"][0]["detail"] and report["overall"] == "warn"


def test_a_check_that_hangs_is_reported_unknown(f):
    import threading
    gate = threading.Event()
    checks = fake_checks("ok") + [("slow", "Slow", "x", lambda env: gate.wait(30) and None)]
    report = health.run_checks(f.env, checks, deadline=0.2)
    gate.set()
    assert [c["status"] for c in report["checks"]] == ["ok", "unknown"]
    assert "did not finish" in report["checks"][1]["detail"]


def test_json_mode_prints_one_object_and_exits_0(f, capsys):
    f.kill("services/crop.py")
    assert health.main(["--json"], env=f.env) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["overall"] == "fail" and len(report["checks"]) == 9


def test_text_mode_exit_codes_and_table(f, capsys):
    assert health.main([], env=f.env) == 0
    out = capsys.readouterr().out
    assert "OK       Research drive   mounted" in out and "What to do" not in out
    f.kill("kiosk-chrome")
    assert health.main([], env=f.env) == 1
    out = capsys.readouterr().out
    assert "WARN     QR screen        QR page is not open" in out
    assert "What to do:\n- QR screen: Open it" in out and "#studio-qr-screen" in out
    f.mounted.discard(f.cache)
    assert health.main([], env=f.env) == 2
    assert "FAIL     External disk" in capsys.readouterr().out


def test_only_runs_the_named_checks_and_rejects_unknown_ids(f, capsys):
    assert health.main(["--json", "--only", "qr_screen"], env=f.env) == 0
    assert [c["id"] for c in json.loads(capsys.readouterr().out)["checks"]] == ["qr_screen"]
    with pytest.raises(SystemExit) as e:
        health.main(["--only", "nope"], env=f.env)
    assert e.value.code == 2


# -------------------------------------------------------------------- setup

def test_build_env_defaults_match_the_drs_mac():
    env = health.build_env(environ={})
    assert env.pipeline_dir == "/Users/signlab/drs" and env.logs_dir == "/Users/signlab/drs/logs"
    assert env.mount_marker == "/Users/signlab/signCollect/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/do_not_remove_for_rclone"
    assert env.cache_disk == "/Volumes/cacheDisk" and env.camera_url == "http://127.0.0.1:8080"
    # both names the Sony SDK checkout goes by
    assert [p.split("/")[3] for p in env.controller_configs] == ["Sony-SDK-MACOS-API", "signlab_Sony-SDK-MACOS-API"]


def test_build_env_options_beat_environment(tmp_path):
    args = SimpleNamespace(pipeline_dir=str(tmp_path), cameras=3, server_url="https://a.test/", require_ethernet=True)
    env = health.build_env(args, {"DRS_HEALTH_PIPELINE_DIR": "/elsewhere", "DRS_HEALTH_CACHE_DISK": "/Volumes/other"})
    assert env.pipeline_dir == str(tmp_path) and env.cache_disk == "/Volumes/other"
    assert env.expected_cameras == 3 and env.server_url == "https://a.test" and env.require_ethernet


def test_server_settings_without_server_config(tmp_path, monkeypatch):
    """The older checkout on the DRS: health.py copied alone, no shared/server_config.py, no .env."""
    monkeypatch.setattr(health, "__file__", str(tmp_path / "tools" / "health.py"))
    assert health.server_settings(str(tmp_path), {}) == ("https://signcollect.nl", "")
    (tmp_path / ".env").write_text("# c\nSIGNCOLLECT_URL=https://dev.test/\nVIDEOFIX_TOKEN='abc'\n")
    assert health.server_settings(str(tmp_path), {}) == ("https://dev.test", "abc")
    assert health.server_settings(str(tmp_path), {"VIDEOFIX_TOKEN": "env"}) == ("https://dev.test", "env")


def test_server_settings_uses_server_config_when_present(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "__file__", str(tmp_path / "tools" / "health.py"))
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "server_config.py").write_text('SERVER_URL = "https://cfg.test"\nVIDEOFIX_TOKEN = "t"\n')
    assert health.server_settings("/nowhere", {}) == ("https://cfg.test", "t")
    (tmp_path / "shared" / "server_config.py").write_text("raise RuntimeError('broken')\n")
    assert health.server_settings("/nowhere", {}) == ("https://signcollect.nl", "")
