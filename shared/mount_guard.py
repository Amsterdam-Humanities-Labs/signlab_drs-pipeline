"""Keep pipeline services from running while DNS or the rclone mount is down.

On 2026-09-24 a DNS outage (uva.data.surf.nl unresolvable) unmounted rclone.
The services kept going: os.makedirs() recreated studioFiles/... on the bare
local mount point, renders and crops landed there instead of on the research
drive, uploads to signcollect.nl failed, and rclone could not remount over the
now non-empty directory for four days.

Services call wait_until_ready() at the start of a cycle (and before uploads),
and create directories under the mount only through safe_makedirs(), which
refuses when the mount is not live instead of writing to local disk.
"""
import os
import socket
import subprocess
import time
from urllib.parse import urlparse

from server_config import SERVER_URL

MOUNT_PATH = "/Users/signlab/signCollect"
PROJECT_DIR = os.path.join(MOUNT_PATH, "AIHR-FGW-TEST-SIGNLAB (Projectfolder)")
DNS_HOSTS = ("uva.data.surf.nl", urlparse(SERVER_URL).hostname)

POLL_SECONDS = 60


class PipelineDownError(OSError):
    """DNS or the rclone mount is unavailable; retry once it is restored."""


def dns_problem(hosts=DNS_HOSTS):
    """Return a description of the first host that does not resolve, or None."""
    for host in hosts:
        try:
            socket.getaddrinfo(host, 443)
        except OSError as e:
            return f"DNS lookup failed for {host}: {e}"
    return None


def mount_problem():
    """Return a description of what is wrong with the rclone mount, or None.

    The checks run in subprocesses with timeouts so a wedged FUSE mount cannot
    hang the calling service.
    """
    try:
        mounts = subprocess.run(['mount'], capture_output=True, text=True, timeout=15).stdout
    except Exception as e:
        return f"could not list mounts: {e}"
    if f" on {MOUNT_PATH} " not in mounts:
        return f"rclone mount is not mounted at {MOUNT_PATH}"
    try:
        result = subprocess.run(['/bin/test', '-d', PROJECT_DIR], timeout=30)
    except subprocess.TimeoutExpired:
        return f"rclone mount is unresponsive ({PROJECT_DIR} timed out)"
    if result.returncode != 0:
        return f"project folder missing on mount: {PROJECT_DIR}"
    return None


def pipeline_problem():
    """Return why the pipeline cannot run right now, or None when it can."""
    return dns_problem() or mount_problem()


def mount_is_live():
    return mount_problem() is None


def wait_until_ready(service, poll_seconds=POLL_SECONDS):
    """Block until DNS resolves and the rclone mount is live."""
    problem = pipeline_problem()
    if problem is None:
        return
    started = time.time()
    print(f"[{service}] Waiting: {problem}", flush=True)
    while True:
        time.sleep(poll_seconds)
        new_problem = pipeline_problem()
        if new_problem is None:
            print(f"[{service}] DNS and rclone mount restored after "
                  f"{(time.time() - started) / 60:.0f} min - resuming", flush=True)
            return
        if new_problem != problem:
            print(f"[{service}] Still waiting: {new_problem}", flush=True)
            problem = new_problem


def safe_makedirs(path):
    """os.makedirs(path, exist_ok=True) that never creates paths on a dead mount.

    Paths outside the mount are created normally.
    """
    path = os.fspath(path)
    if os.path.abspath(path).startswith(MOUNT_PATH + os.sep):
        problem = mount_problem()
        if problem:
            raise PipelineDownError(f"Refusing to create {path}: {problem}")
    os.makedirs(path, exist_ok=True)
