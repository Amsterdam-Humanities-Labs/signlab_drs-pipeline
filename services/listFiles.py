import os
import re
import sys
import json
import time
import hashlib
import requests
from datetime import datetime
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, '/Users/signlab/drs/shared')
from signcollect_monitor import SignCollectMonitor  # Import the monitor client

CR_URL = "https://signcollect.nl/CR.php"
GLOSIDS_CACHE_DIR = "/Users/signlab/drs/logs/glosids_cache"
SIDECAR_READ_WORKERS = 16


def fetch_cr_glosids_by_date():
    """Fetch expected glosIds per date from CR.php. Returns {date_str: [glosId_str, ...]} or {} on failure."""
    try:
        response = requests.get(CR_URL, timeout=30)
        response.raise_for_status()
        payload = response.json()
    except Exception as e:
        print(f"Error fetching {CR_URL}: {e}")
        return {}

    result = {}
    for day in payload.get("days", []):
        date = day.get("date")
        glos = day.get("glosIds", [])
        if date:
            result[date] = [str(g) for g in glos]
    return result


def _scan_lmr_sidecar_names(raw_path):
    """Return {'L': [...], 'M': [...], 'R': [...]} of .json sidecar filenames (single listdir)."""
    by_letter = {'L': [], 'M': [], 'R': []}
    try:
        entries = os.listdir(raw_path)
    except OSError:
        return by_letter
    for name in entries:
        if name.endswith('.json') and name and name[0] in by_letter:
            by_letter[name[0]].append(name)
    return by_letter


def _filename_signature(by_letter):
    """Hash the sorted filename lists per camera. Changes when sidecars are added/removed/renamed."""
    return {
        letter: hashlib.md5("\n".join(sorted(names)).encode()).hexdigest()
        for letter, names in by_letter.items()
    }


def _read_sidecar_glosid(filepath):
    """Read one sidecar JSON and return its first element as a string, or None on failure."""
    try:
        with open(filepath, 'r') as f:
            data = json.load(f)
        if isinstance(data, list) and len(data) > 0:
            return str(data[0])
    except (json.JSONDecodeError, OSError):
        return None
    return None


def _load_glosids_cache(date):
    path = os.path.join(GLOSIDS_CACHE_DIR, f"{date}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _save_glosids_cache(date, cache_obj):
    try:
        os.makedirs(GLOSIDS_CACHE_DIR, exist_ok=True)
        path = os.path.join(GLOSIDS_CACHE_DIR, f"{date}.json")
        tmp = path + ".tmp"
        with open(tmp, 'w') as f:
            json.dump(cache_obj, f)
        os.replace(tmp, path)
    except OSError as e:
        print(f"Failed to save glosids cache for {date}: {e}")


def _read_lmr_glosids(raw_path, date):
    """Read L/M/R glosIds for a date using local disk cache; parallel sidecar reads on cache miss.

    Cache invalidates when the hash of the sorted .json sidecar filename list changes for any camera.
    Returns {'L': [...], 'M': [...], 'R': [...]} of glosId strings.
    """
    by_letter = _scan_lmr_sidecar_names(raw_path)
    signature = _filename_signature(by_letter)

    cached = _load_glosids_cache(date)
    if cached and cached.get('signature') == signature:
        return {
            'L': cached.get('L', []),
            'M': cached.get('M', []),
            'R': cached.get('R', []),
        }

    # Cache miss: read all sidecars in parallel (rclone is latency-bound, not bandwidth-bound)
    tasks = []
    for letter, names in by_letter.items():
        for name in names:
            tasks.append((letter, os.path.join(raw_path, name)))

    results = {'L': [], 'M': [], 'R': []}
    if tasks:
        print(f"Reading {len(tasks)} sidecar JSONs for {date} (parallel, workers={SIDECAR_READ_WORKERS})...")
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=SIDECAR_READ_WORKERS) as executor:
            futures = {executor.submit(_read_sidecar_glosid, path): letter for letter, path in tasks}
            for fut in futures:
                letter = futures[fut]
                try:
                    gid = fut.result()
                except Exception:
                    gid = None
                if gid is not None:
                    results[letter].append(gid)
        print(f"  done in {time.time()-t0:.1f}s (L={len(results['L'])}, M={len(results['M'])}, R={len(results['R'])})")

    _save_glosids_cache(date, {
        'signature': signature,
        'L': results['L'],
        'M': results['M'],
        'R': results['R'],
        'updated_at': datetime.now().isoformat(),
    })
    return results


def compute_glosid_coverage(raw_path, date, expected_glosids):
    """Compute per-camera (L/M/R) and combined glosId coverage percentages for a raw folder.

    Returns a dict with:
        expected_count: int (count of distinct glosIds CR.php expects for this date)
        l_pct, m_pct, r_pct: percentage of expected glosIds present in each camera's sidecars
        all_three_pct: percentage of expected glosIds present in ALL of L, M, AND R
    Returns None if expected_glosids is empty (nothing to check against).
    """
    if not expected_glosids:
        return None
    expected_set = set(expected_glosids)
    glosids = _read_lmr_glosids(raw_path, date)
    l_set = set(glosids['L'])
    m_set = set(glosids['M'])
    r_set = set(glosids['R'])
    total = len(expected_set)

    def pct(matched):
        return round(100.0 * matched / total, 1) if total else 0.0

    return {
        "expected_count": total,
        "l_pct": pct(len(expected_set & l_set)),
        "m_pct": pct(len(expected_set & m_set)),
        "r_pct": pct(len(expected_set & r_set)),
        "all_three_pct": pct(len(expected_set & l_set & m_set & r_set)),
    }

# Initialize the monitor
monitor = SignCollectMonitor(
    client_id='drs-file-lister',
    client_name='DRS File Lister',
    description='File counting and status reporting service',
    heartbeat_interval=3600
)

def list_and_count_files():
    base_dir = "/Users/signlab/signCollect/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles"

    if not os.path.exists(base_dir):
        return {"error": f"Directory not found: {base_dir}"}

    # Get all date folders and sort them
    date_folders = []
    for item in os.listdir(base_dir):
        item_path = os.path.join(base_dir, item)
        if os.path.isdir(item_path) and re.match(r'\d{4}-\d{2}-\d{2}', item):
            date_folders.append(item)

    # Sort and get last 10
    date_folders.sort()
    last_10_folders = date_folders[-10:]

    # Fetch expected glosIds from CR.php once per run (used for all folders below)
    cr_glosids_by_date = fetch_cr_glosids_by_date()

    result = {
        "timestamp": datetime.now().isoformat(),
        "base_directory": base_dir,
        "folders": []
    }

    for folder in last_10_folders:
        folder_path = os.path.join(base_dir, folder)
        raw_path = os.path.join(folder_path, "raw")

        folder_data = {"date": folder}

        if not os.path.exists(raw_path):
            folder_data["status"] = "no_raw_folder"
            folder_data["files"] = {}
            folder_data["total"] = 0
            folder_data["is_ok"] = False
        else:
            # Count files by starting letter
            counter = Counter()
            total_files = 0

            for filename in os.listdir(raw_path):
                if filename.endswith('.MP4') and filename[0] in 'ABLMR':
                    counter[filename[0]] += 1
                    total_files += 1

            if total_files == 0:
                folder_data["status"] = "no_matching_files"
                folder_data["files"] = {}
                folder_data["total"] = 0
                folder_data["is_ok"] = False
            else:
                folder_data["status"] = "ok"
                folder_data["files"] = dict(counter)
                folder_data["total"] = total_files

                # Check if all counts are equal
                count_values = [counter[letter] for letter in 'ABLMR' if counter[letter] > 0]
                folder_data["is_ok"] = len(count_values) > 1 and len(set(count_values)) == 1

            # Cross-check sidecar glosIds against CR.php expected list for this date (L/M/R only)
            expected = cr_glosids_by_date.get(folder, [])
            coverage = compute_glosid_coverage(raw_path, folder, expected)
            if coverage is not None:
                folder_data["glosid_coverage"] = coverage

        result["folders"].append(folder_data)

    return result

def post_results(data):
    url = "https://signcollect.nl/listFiles.php"
    try:
        response = requests.post(url, json=data, timeout=30)
        print(f"Posted to {url} - Status: {response.status_code}")
        return response.status_code == 200
    except Exception as e:
        print(f"Error posting to {url}: {e}")
        return False

if __name__ == "__main__":
    # Register with monitoring system
    monitor.register()

    while True:
        # Send heartbeat at start of each cycle
        monitor.send_heartbeat()

        print(f"Running file count at {datetime.now()}")
        data = list_and_count_files()

        # Print summary to console
        if "error" in data:
            print(data["error"])
        else:
            for folder in data["folders"]:
                status_symbol = "OK" if folder["is_ok"] else "ERROR"
                print(f"{folder['date']}: Total {folder['total']} files - {status_symbol}")

        # Post to server
        success = post_results(data)
        if success:
            print("Successfully posted results")
        else:
            print("Failed to post results")

        print("Sleeping for 1 hour...")
        time.sleep(3600)  # Sleep for 1 hour