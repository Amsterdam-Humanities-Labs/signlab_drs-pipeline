import os
import re
import sys
import json
import time
import requests
from datetime import datetime
from collections import Counter
sys.path.insert(0, '/Users/signlab/drs/shared')
from signcollect_monitor import SignCollectMonitor  # Import the monitor client

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