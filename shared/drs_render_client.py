"""
DRS Render Coordination Client

Coordinates rendering between multiple DaVinci Resolve instances.
Uses <SIGNCOLLECT_URL>/drs_ep/api.php to claim/release files
so two machines don't render the same file.
"""
import requests
import socket
import json
from datetime import datetime
from typing import Optional, List, Dict, Any
from server_config import server_url

API_URL = server_url("drs_ep/api.php")


class DRSRenderClient:
    """
    Client for coordinating DaVinci Resolve rendering across multiple machines.

    Usage:
        client = DRSRenderClient(machine_name="mac-studio")

        # Before rendering, try to claim the file
        if client.claim_file("M20260216_4332.MP4"):
            # We got the lock - safe to render
            render(file)
            client.complete_file("M20260216_4332.MP4")
        else:
            # Another machine is already rendering this file
            skip(file)

        # Batch claim: claim multiple files at once, get back which ones you got
        claimed = client.claim_files(["M20260216_4332.MP4", "L20260216_5915.MP4"])
        # claimed = ["M20260216_4332.MP4"]  (only the ones you got)

        # Check what's currently being rendered
        active = client.get_active_claims()

        # Release a claim if rendering failed (without marking complete)
        client.release_file("M20260216_4332.MP4")
    """

    def __init__(self, machine_name: Optional[str] = None, api_url: str = API_URL):
        self.api_url = api_url
        self.machine_name = machine_name or socket.gethostname()

    def _request(self, action: str, data: Optional[Dict] = None, method: str = "POST") -> Dict:
        """Make a request to the API."""
        params = {"action": action}
        try:
            if method == "GET":
                resp = requests.get(self.api_url, params={**params, **(data or {})}, timeout=10)
            else:
                resp = requests.post(self.api_url, params=params, json=data, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            print(f"DRS API error ({action}): {e}")
            return {"success": False, "error": str(e)}

    # ── Single file operations ──────────────────────────────────────────

    def claim_file(self, filename: str) -> bool:
        """
        Try to claim a file for rendering. Returns True if claim succeeded
        (no other machine is rendering it), False if already claimed.
        """
        result = self._request("claim", {
            "filename": filename,
            "machine": self.machine_name,
        })
        return result.get("success", False)

    def complete_file(self, filename: str) -> bool:
        """Mark a file as successfully rendered."""
        result = self._request("complete", {
            "filename": filename,
            "machine": self.machine_name,
        })
        return result.get("success", False)

    def release_file(self, filename: str) -> bool:
        """Release a claim without marking complete (e.g., render failed)."""
        result = self._request("release", {
            "filename": filename,
            "machine": self.machine_name,
        })
        return result.get("success", False)

    # ── Batch operations ────────────────────────────────────────────────

    def claim_files(self, filenames: List[str]) -> List[str]:
        """
        Try to claim multiple files at once. Returns list of filenames
        that were successfully claimed (not already taken by another machine).
        """
        result = self._request("claim_batch", {
            "filenames": filenames,
            "machine": self.machine_name,
        })
        return result.get("claimed", [])

    def complete_files(self, filenames: List[str]) -> bool:
        """Mark multiple files as successfully rendered."""
        result = self._request("complete_batch", {
            "filenames": filenames,
            "machine": self.machine_name,
        })
        return result.get("success", False)

    def release_files(self, filenames: List[str]) -> bool:
        """Release claims on multiple files."""
        result = self._request("release_batch", {
            "filenames": filenames,
            "machine": self.machine_name,
        })
        return result.get("success", False)

    # ── Query operations ────────────────────────────────────────────────

    def get_active_claims(self) -> List[Dict]:
        """Get all files currently claimed (being rendered)."""
        result = self._request("get_active", method="GET")
        return result.get("claims", [])

    def get_completed(self, date: Optional[str] = None) -> List[Dict]:
        """Get completed files, optionally filtered by date (YYYY-MM-DD)."""
        data = {}
        if date:
            data["date"] = date
        result = self._request("get_completed", data=data, method="GET")
        return result.get("files", [])

    def is_claimed(self, filename: str) -> bool:
        """Check if a file is currently claimed by any machine."""
        result = self._request("check", {"filename": filename}, method="GET")
        return result.get("claimed", False)

    def get_stats(self) -> Dict:
        """Get rendering statistics per machine."""
        result = self._request("stats", method="GET")
        return result.get("stats", {})

    # ── Maintenance ─────────────────────────────────────────────────────

    def cleanup_stale(self, max_age_minutes: int = 60) -> Dict:
        """
        Release claims older than max_age_minutes (crashed renders).
        Called periodically to prevent deadlocks.
        """
        result = self._request("cleanup", {
            "max_age_minutes": max_age_minutes,
        })
        return result


# ── Convenience for integration into batch scripts ──────────────────────

def get_client(machine_name: Optional[str] = None) -> DRSRenderClient:
    """Get a DRS render client instance."""
    return DRSRenderClient(machine_name=machine_name)


if __name__ == "__main__":
    # Quick test
    client = DRSRenderClient(machine_name="test-machine")

    print("Testing DRS Render Coordination API...")
    print(f"Machine: {client.machine_name}")
    print(f"API: {client.api_url}")

    # Test claim
    print("\n1. Claiming test file...")
    ok = client.claim_file("TEST_FILE.MP4")
    print(f"   Claim result: {ok}")

    # Test check
    print("2. Checking if claimed...")
    claimed = client.is_claimed("TEST_FILE.MP4")
    print(f"   Is claimed: {claimed}")

    # Test active claims
    print("3. Getting active claims...")
    active = client.get_active_claims()
    print(f"   Active: {active}")

    # Test complete
    print("4. Completing test file...")
    ok = client.complete_file("TEST_FILE.MP4")
    print(f"   Complete result: {ok}")

    # Test stats
    print("5. Getting stats...")
    stats = client.get_stats()
    print(f"   Stats: {stats}")

    # Test cleanup
    print("6. Running cleanup...")
    result = client.cleanup_stale()
    print(f"   Cleanup result: {result}")
