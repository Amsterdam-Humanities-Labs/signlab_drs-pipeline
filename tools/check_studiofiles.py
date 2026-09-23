#!/usr/bin/env python3

import re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import subprocess
import logging
import logging.handlers
import shutil
import os
import psutil
import sys
import mysql.connector
import json
from datetime import datetime


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        # 5 MB x 5, the same policy as setup_rotating_logger in the heartbeat client.
        logging.handlers.RotatingFileHandler(
            '/web/helpScripts/move_videos.log', maxBytes=5 * 1024 * 1024, backupCount=5),
        logging.StreamHandler()
    ]
)

# Base directory containing the date-named folders
BASE_DIR = "/web/gebarenoverleg_media/studioFiles"
RAW_SUBDIR = "raw"

# Directory for mini raw files
MINI_RAW_DIR = "/web/gebarenoverleg_media/studioFilesMini/raw"

# Regular expression patterns
DATE_DIR_REGEX = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Regex to include filenames starting with A, B, L, M, or R
FILENAME_DATE_REGEX = re.compile(r"^[ABLMR](\d{8})_\d+\.MP4$", re.IGNORECASE)


def is_already_running(script_name):
    """
    Check if another instance of the script is already running.

    Parameters:
    - script_name: Name of the script file (e.g., 'qrConvert.py')

    Returns:
    - True if another instance is running, False otherwise.
    """
    current_pid = os.getpid()
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            # Check if process name matches and it's not the current process
            if 'python' in proc.info['name'] or 'python3' in proc.info['name']:
                cmdline = proc.info['cmdline']
                print(cmdline)
                print(proc.info['pid'])
                print(current_pid)
                if cmdline is not None and len(cmdline) > 1:
                    if "check_studiofiles" in cmdline[1] and proc.info['pid'] != current_pid:
                        return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return False

def rclone_move(src: Path, dest: Path):
    """
    Use rclone to move a file from src to dest. Removes existing files at dest before moving.
    """
    try:
        cmd = [
            'rclone', 'move', str(src), str(dest.parent),
            '--transfers=16', '--log-level=DEBUG', '--timeout=1m'
        ]
        subprocess.run(cmd, check=True, timeout=60)
        # subprocess.run(cmd, check=True)
        logging.info(f"Rclone moved: {src} -> {dest}")
    except subprocess.CalledProcessError as e:
        logging.error(f"Rclone failed to move {src} to {dest}: {e}")

def process_file(file_path: Path, folder_date: str):
    """
    Process a single file: check if it needs to be moved and perform the move using rclone.
    """
    filename = file_path.name

    # Check if filename contains the folder date
    if folder_date not in filename:
        logging.info(f"File mismatch: {file_path}")

        # Extract date from filename
        match = FILENAME_DATE_REGEX.search(filename)
        if not match:
            logging.warning(f"Filename does not contain a valid date: {filename}. Skipping.")
            return

        file_date_str = match.group(1)  # e.g., "20241017"
        try:
            # Convert to YYYY-MM-DD format
            file_date_formatted = f"{file_date_str[:4]}-{file_date_str[4:6]}-{file_date_str[6:8]}"
        except IndexError:
            logging.warning(f"Invalid date format in filename: {filename}. Skipping.")
            return

        target_dir = Path(BASE_DIR) / file_date_formatted / RAW_SUBDIR
        target_file = target_dir / filename

        print("target_dir: ", target_dir)
        # Ensure target directory exists
        target_dir.mkdir(parents=True, exist_ok=True)

        print("moving file: ", file_path, " to ", target_file)
        # Perform the move using rclone
        rclone_move(file_path, target_file)

def copy_and_rename(file: Path):
    """
    Copy the file to MINI_RAW_DIR with the extension in lowercase if it doesn't already exist.
    """
    dest_filename = file.stem + file.suffix.lower()
    dest_path = Path(MINI_RAW_DIR) / dest_filename

    if dest_path.exists():
        logging.info(f"File already exists, skipping: {dest_path}")
        print("File already exists, skipping: {dest_path}")
        return

    try:
        shutil.copy2(file, dest_path)
        logging.info(f"Copied and renamed: {file} -> {dest_path}")
    except Exception as e:
        logging.error(f"Failed to copy {file} to {dest_path}: {e}")

def process_additional_folder(folder: Path, subfolder_name: str):
    """
    Copy files from 'converted' or 'thumbnails' subfolders to MINI_RAW_DIR with lowercase extensions.
    """
    subfolder = folder / subfolder_name
    if not subfolder.is_dir():
        logging.info(f"No {subfolder_name} folder in {folder}.")
        return

    logging.info(f"Processing subfolder: {subfolder}")

    files = [file for file in subfolder.iterdir() if file.is_file()]

    # Process files concurrently within the subfolder
    with ThreadPoolExecutor(max_workers=1) as executor:
        futures = [executor.submit(copy_and_rename, file) for file in files]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                logging.error(f"Error copying file from {subfolder_name}: {e}")

def process_folder(folder: Path):
    """
    Process a single date-named folder.
    """
    folder_date = folder.name.replace("-", "")  # e.g., "20241017"
    raw_dir = folder / RAW_SUBDIR

    if not raw_dir.is_dir():
        logging.warning(f"No raw directory in folder: {folder}. Skipping.")
        return

    logging.info(f"Checking folder: {folder_date}")

    files = [file for file in raw_dir.iterdir() if file.is_file()]

    # Process files concurrently within the folderf
    with ThreadPoolExecutor(max_workers=1) as executor:
        futures = [executor.submit(process_file, file, folder_date) for file in files]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                logging.error(f"Error processing file: {e}")

    # Process additional folders: 'converted' and 'thumbnails'
    for subfolder_name in ['converted', 'thumbnails']:
        process_additional_folder(folder, subfolder_name)


# MySQL configuration. Credentials come from the environment or the untracked
# .env in the repo root (DB_HOST, DB_USER, DB_PASSWORD, DB_NAME). Never a literal.
def _load_env() -> None:
    env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
    try:
        with open(env_file) as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ.setdefault(key.strip(), value.strip().strip('"\''))
    except OSError:
        pass


_load_env()
db_config = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'user': os.getenv('DB_USER', 'user'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_NAME', 'admin_gebarenoverleg'),
}


def get_db_connection():
    try:
        connection = mysql.connector.connect(**db_config)
        if connection.is_connected():
            print("Successfully connected to the database.")
            return connection
    except mysql.connector.Error as err:
        print(f"Error connecting to MySQL: {err}")
    return None


def count_studio_files(folder: Path):
    #here we want to count number of L, M, R, A, B files in the folder and put the count in table studio_data
    L_count = 0
    M_count = 0
    R_count = 0
    A_count = 0
    B_count = 0
    countArray = {}
    
    raw_dir = folder / "raw"
    
    #get date from the filepath, for example /web/gebarenoverleg_media/studioFiles/2024-11-13/raw
    
    
    date_str = folder.name  # e.g., "2024-11-13"
    try:
        datum_file = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        logging.error(f"Invalid date format in folder name: {date_str}")
        return
    
    datum_file = datum_file.strftime("%Y%m%d")
    
    for file in raw_dir.iterdir():
        if file.is_file() and file.suffix.lower() == '.mp4':
            filename = file.name
            if filename.startswith('L'):
                if datum_file in filename:
                  L_count += 1
            elif filename.startswith('M'):
                if datum_file in filename:
                    M_count += 1
            elif filename.startswith('R'):
                if datum_file in filename:
                    R_count += 1
            elif filename.startswith('A'):
                if datum_file in filename:
                    A_count += 1
            elif filename.startswith('B'):
                if datum_file in filename:
                    B_count += 1
                
    #save the counts in a dictionary based on date
    countArray[folder.name] = [L_count, M_count, R_count, A_count, B_count]
    
    #open json file and read the data, if folder.name is still not in the json file, add it otherwise update the counts
    with open('/web/studio_data.json', 'r') as file:
       data = json.load(file)
       if folder.name not in data:
           data[folder.name] = [L_count, M_count, R_count, A_count, B_count]
       else:
           data[folder.name] = [L_count, M_count, R_count, A_count, B_count]
           
    #reorder data based on date from folder.name
    data = dict(sorted(data.items()))       
    #reverse
    data = dict(reversed(data.items()))
    
    
    with open('/web/studio_data.json', 'w') as file:
       json.dump(data, file)
    
                
                
    #then update the studio_data table with the counts based on date of the folder in field date of the table
    date = folder.name
    
    conn = get_db_connection()
    if not conn:
        print("Failed to connect to the database. Exiting.")
        return
    cursor = conn.cursor(dictionary=True)
    sql = "UPDATE studio_data SET L_count = %s, M_count = %s, R_count = %s, A_count = %s, B_count = %s WHERE date = %s"
    cursor.execute(sql, (L_count, M_count, R_count, A_count, B_count, date))
    print(L_count, M_count, R_count, A_count, B_count, date)
    conn.commit()
    conn.close()
    
    

def main():
    base_path = Path(BASE_DIR)

    if not base_path.is_dir():
        logging.error(f"Base directory does not exist: {BASE_DIR}")
        return

    # Ensure MINI_RAW_DIR exists
    Path(MINI_RAW_DIR).mkdir(parents=True, exist_ok=True)

    # Find all directories in base_dir that match the date pattern YYYY-MM-DD
    date_folders = [folder for folder in base_path.iterdir() if folder.is_dir() and DATE_DIR_REGEX.match(folder.name)]
    
    #reverse the date_folders list
    date_folders = date_folders[::-1]
    
    for folder in date_folders:
        print(f"Found date-named folder: {folder}")

    if not date_folders:
        logging.info("No date-named directories found.")
        return

    # Use ThreadPoolExecutor to process folders concurrently
    with ThreadPoolExecutor(max_workers=1) as executor:  # Adjust max_workers as needed
        futures = [executor.submit(process_folder, folder) for folder in date_folders]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                logging.error(f"Error processing folder: {e}")
    for folder in date_folders:
        count_studio_files(folder)

    logging.info("All moves and copies completed.")

if __name__ == "__main__":
        # Check if another instance is running
    script_name = os.path.basename(__file__)
    script_name = os.path.splitext(script_name)[0]
    if is_already_running(script_name):
        print(f"Another instance of {script_name} is already running. Exiting.")
        sys.exit(0)
    # print("Starting script")
    main()
