import cv2
import os
import json
import mysql.connector
from datetime import datetime, date, timedelta
import re
from collections import defaultdict
from PIL import Image
from qreader import QReader
import numpy as np
import psutil
import sys

def is_already_running(script_name):
    """Check if another instance of the script is already running."""
    current_pid = os.getpid()
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if 'python' in proc.info['name'] or 'python3' in proc.info['name']:
                cmdline = proc.info['cmdline']
                if cmdline is not None and len(cmdline) > 1:
                    if "qrConvert" in cmdline[1] and proc.info['pid'] != current_pid:
                        return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return False

def preprocess_image(frame):
    """Preprocess the image for better QR code detection."""
    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    ms = str(int(datetime.now().timestamp() * 1000))
    Image.fromarray(frame_rgb).save(f"preprocessed_frame_{ms}.png")
    return frame_rgb

def extract_qr_from_video(video_path, qreader):
    """Extract QR code data from a single video file."""
    cap = cv2.VideoCapture(video_path)
    frame_count = 140
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_count)
    
    while cap.isOpened() and frame_count <= 140:
        ret, frame = cap.read()
        if not ret:
            break
            
        preprocessed_frame = preprocess_image(frame)
        
        try:
            detections = qreader.detect(image=preprocessed_frame)
            for detection in detections:
                x1, y1, x2, y2 = detection['bbox_xyxy']
                x1, y1, x2, y2 = int(x1-10), int(y1-10), int(x2+10), int(y2+10)
                
                cropped_image = preprocessed_frame[y1:y2, x1:x2]
                decoded_text = qreader.detect_and_decode(image=cropped_image)
                
                if decoded_text and decoded_text[0] is not None:
                    try:
                        decoded_data = json.loads(decoded_text[0])
                        cap.release()
                        return decoded_data
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            print(f"QReader error: {e}")
            
        frame_count += 50
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_count)
    
    cap.release()
    return None

def get_existing_files_from_db():
    """Get all existing filenames and complete records from the database."""
    conn = get_db_connection()
    if not conn:
        return set(), set()

    cursor = conn.cursor()
    try:
        # Calculate cutoff date (4 months ago)
        today = datetime.now().date()
        cutoff_date = today - timedelta(days=90)  # Approximate 4 months

        # Get all files from database from the past 4 months and convert to .wav for comparison
        cursor.execute("""
            SELECT m_file, l_file, r_file, a_file, b_file, date, time, m_transcription
            FROM matched_transcriptions
            WHERE (m_file IS NOT NULL OR l_file IS NOT NULL OR r_file IS NOT NULL
                   OR a_file IS NOT NULL OR b_file IS NOT NULL)
            AND date >= %s
        """, (cutoff_date,))
        rows = cursor.fetchall()

        existing_files = set()
        complete_records = set()  # (date, time, glosId) tuples where all 5 files exist

        for row in rows:
            m_file, l_file, r_file, a_file, b_file, rec_date, rec_time, glosId = row

            # Track individual files
            for file in [m_file, l_file, r_file, a_file, b_file]:
                if file:
                    wav_file = os.path.splitext(file)[0] + '.wav'
                    existing_files.add(wav_file)

            # Check if record is complete (all 5 camera files present)
            if m_file and l_file and r_file and a_file and b_file:
                complete_records.add((rec_date, rec_time, str(glosId)))

        return existing_files, complete_records
    except mysql.connector.Error as err:
        print(f"Error getting existing files from database: {err}")
        return set(), set()
    finally:
        cursor.close()
        conn.close()

def process_mp4_files():
    """Process all MP4 files and extract QR data."""
    qreader = QReader()
    directory_path = "/web/gebarenoverleg_media/studioFilesMini/raw"
    
    # Calculate cutoff timestamp (3 months ago)
    import time
    today = datetime.now()
    cutoff_timestamp = (today - timedelta(days=14)).timestamp()  # Approximate 3 months
    
    # Get existing files from database to avoid reprocessing
    existing_files, complete_records = get_existing_files_from_db()
    print(f"Found {len(existing_files)} existing files and {len(complete_records)} complete records in database")
    
    video_data = []
    
    # Get files filtered by modification time (much more efficient)
    try:
        for filename in os.listdir(directory_path):
            if not filename.upper().endswith('.MP4'):
                continue
                
            if "h264" in filename:
                continue
            
            # Check if file already exists in database
            wav_filename = os.path.splitext(filename)[0] + '.wav'
            if wav_filename in existing_files:
                # print(f"File {wav_filename} already exists in database. Skipping.")
                continue
            
            file_path = os.path.join(directory_path, filename)
            
            # Check file modification time first (fastest filter)
            file_mtime = os.path.getmtime(file_path)
            if file_mtime < cutoff_timestamp:
                continue  # Skip old files immediately
            
            # Parse filename to get date for JSON path
            match = re.match(r'^([A-Z])(\d{8})_(\d+)\.mp4$', filename, re.IGNORECASE)
            if not match:
                # print(f"Filename {filename} does not match expected pattern.")
                continue
                
            camera, date_str, increment = match.groups()
            
            # Convert date_str to proper format for directory path
            date_formatted = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
            
            # Check if JSON file already exists in the correct directory
            json_filename = os.path.splitext(filename)[0] + '.json'
            json_path = f"/web/gebarenoverleg_media/studioFiles/{date_formatted}/raw/{json_filename}"
            
            if os.path.exists(json_path):
                # print(f"JSON file already exists for {filename}. Reading existing data.")
                try:
                    with open(json_path, 'r') as json_file:
                        existing_qr_data = json.load(json_file)
                    
                    # Handle both formats: [glosId, type, time] and [[glosId, type, time]]
                    if existing_qr_data:
                        # If it's a nested array, take the first element
                        if isinstance(existing_qr_data[0], list) and len(existing_qr_data[0]) == 3:
                            glosId, selectedType, time_str = existing_qr_data[0]
                        # If it's a simple array with 3 elements
                        elif len(existing_qr_data) == 3:
                            glosId, selectedType, time_str = existing_qr_data
                        else:
                            print(f"Existing JSON file for {filename} has unexpected format.")
                            continue
                        
                        # Check if glosId is an integer
                        try:
                            int(glosId)
                        except (ValueError, TypeError):
                            print(f"Invalid glosId '{glosId}' in JSON file for {filename}. Not an integer. Removing JSON file.")
                            try:
                                os.remove(json_path)
                                print(f"Removed invalid JSON file: {json_path}")
                            except Exception as e:
                                print(f"Error removing JSON file {json_path}: {e}")
                            # Continue to process the video file normally
                            print(f"Processing: {filename}")
                            qr_data = extract_qr_from_video(file_path, qreader)
                            
                            if qr_data and len(qr_data) == 3:
                                glosId, selectedType, time_str = qr_data
                                
                                # Convert selectedType
                                if selectedType == "glos":
                                    selectedType = "extern"

                                # Check if this record is already complete
                                date_key = (datetime.strptime(date_str, '%Y%m%d').date(), time_str, str(glosId))
                                if date_key in complete_records:
                                    print(f"Record already complete for {glosId} at {date_str}, {time_str}. Skipping.")
                                    continue

                                video_data.append({
                                    'filename': os.path.splitext(filename)[0] + '.wav',
                                    'camera': camera.upper(),
                                    'date_str': date_str,
                                    'date': datetime.strptime(date_str, '%Y%m%d').date(),
                                    'increment': int(increment),
                                    'glosId': glosId,
                                    'selectedType': selectedType,
                                    'time_str': time_str
                                })

                                # Save JSON file immediately after successful QR extraction to correct directory
                                try:
                                    # Ensure directory exists
                                    os.makedirs(os.path.dirname(json_path), exist_ok=True)

                                    with open(json_path, 'w') as json_file:
                                        json.dump([glosId, selectedType, time_str], json_file)
                                    print(f"Saved QR data to {json_path}")
                                except Exception as e:
                                    print(f"Error writing JSON file for {filename}: {e}")

                                print(f"Found QR: {glosId}, {selectedType}, {time_str}")
                            else:
                                # Save empty JSON file if no QR code found to correct directory
                                try:
                                    # Ensure directory exists
                                    os.makedirs(os.path.dirname(json_path), exist_ok=True)
                                    
                                    with open(json_path, 'w') as json_file:
                                        json.dump([], json_file)
                                    print(f"Saved empty array to {json_path} as no QR codes were found in {filename}.")
                                except Exception as e:
                                    print(f"Error writing JSON file for {filename}: {e}")
                            continue
                        
                        # Convert selectedType
                        if selectedType == "glos":
                            selectedType = "extern"

                        # Check if this record is already complete
                        date_key = (datetime.strptime(date_str, '%Y%m%d').date(), time_str, str(glosId))
                        if date_key in complete_records:
                            print(f"Record already complete for {glosId} at {date_str}, {time_str}. Skipping.")
                            continue

                        video_data.append({
                            'filename': os.path.splitext(filename)[0] + '.wav',
                            'camera': camera.upper(),
                            'date_str': date_str,
                            'date': datetime.strptime(date_str, '%Y%m%d').date(),
                            'increment': int(increment),
                            'glosId': glosId,
                            'selectedType': selectedType,
                            'time_str': time_str
                        })

                        print(f"Added existing QR data: {glosId}, {selectedType}, {time_str}")
                    else:
                        print(f"Existing JSON file for {filename} is empty or invalid.")
                except Exception as e:
                    print(f"Error reading existing JSON file for {filename}: {e}")
                continue
            
            print(f"Processing: {filename}")
            
            # Extract QR data
            qr_data = extract_qr_from_video(file_path, qreader)
            
            if qr_data and len(qr_data) == 3:
                glosId, selectedType, time_str = qr_data

                # Convert selectedType
                if selectedType == "glos":
                    selectedType = "extern"

                # Check if this record is already complete
                date_key = (datetime.strptime(date_str, '%Y%m%d').date(), time_str, str(glosId))
                if date_key in complete_records:
                    print(f"Record already complete for {glosId} at {date_str}, {time_str}. Skipping.")
                    continue

                video_data.append({
                    'filename': os.path.splitext(filename)[0] + '.wav',
                    'camera': camera.upper(),
                    'date_str': date_str,
                    'date': datetime.strptime(date_str, '%Y%m%d').date(),
                    'increment': int(increment),
                    'glosId': glosId,
                    'selectedType': selectedType,
                    'time_str': time_str
                })

                # Save JSON file immediately after successful QR extraction to correct directory
                try:
                    # Ensure directory exists
                    os.makedirs(os.path.dirname(json_path), exist_ok=True)
                    
                    with open(json_path, 'w') as json_file:
                        json.dump([glosId, selectedType, time_str], json_file)
                    print(f"Saved QR data to {json_path}")
                except Exception as e:
                    print(f"Error writing JSON file for {filename}: {e}")
                
                print(f"Found QR: {glosId}, {selectedType}, {time_str}")
            else:
                # Save empty JSON file if no QR code found to correct directory
                try:
                    # Ensure directory exists
                    os.makedirs(os.path.dirname(json_path), exist_ok=True)
                    
                    with open(json_path, 'w') as json_file:
                        json.dump([], json_file)
                    print(f"Saved empty array to {json_path} as no QR codes were found in {filename}.")
                except Exception as e:
                    print(f"Error writing JSON file for {filename}: {e}")
    
    except FileNotFoundError:
        print(f"Directory not found: {directory_path}")
        return video_data
    except PermissionError:
        print(f"Permission denied accessing: {directory_path}")
        return video_data
    
    return video_data

def group_videos_by_transcription(video_data):
    """Group videos by date, time_str, and glosId."""
    groups = defaultdict(list)
    
    for video in video_data:
        key = (video['date'], video['time_str'], video['glosId'])
        groups[key].append(video)
    
    # Sort each group by increment
    for key in groups:
        groups[key].sort(key=lambda x: x['increment'])
    
    return groups

def update_database(groups, cursor, conn):
    """Update matched_transcriptions table with grouped video data."""
    for (date_obj, time_str, glosId), videos in groups.items():
        # Organize videos by camera
        cameras = {}
        selectedType = None
        
        for video in videos:
            cameras[video['camera'].lower() + '_file'] = video['filename']
            if selectedType is None:
                selectedType = video['selectedType']
        
        # # Ensure we have m_file (required)
        # if 'm_file' not in cameras:
        #     print(f"No M camera file for group {date_obj}, {time_str}, {glosId}")
        #     continue
        
        # Check if record exists
        check_sql = "SELECT * FROM matched_transcriptions WHERE date = %s AND time = %s AND m_transcription = %s"
        cursor.execute(check_sql, (date_obj, time_str, glosId))
        existing = cursor.fetchone()
        
        if existing:
            # Build update fields only for values that actually changed
            update_fields = []
            params = []

            # Check each camera file field
            for field, value in cameras.items():
                if existing.get(field) != value:
                    update_fields.append(f"{field} = %s")
                    params.append(value)

            # Check transcription fields
            for camera in ['m', 'l', 'r', 'a', 'b']:
                field_name = f"{camera}_transcription"
                if str(existing.get(field_name)) != str(glosId):
                    update_fields.append(f"{field_name} = %s")
                    params.append(glosId)

            # Check definitive_outcome and zOg
            if str(existing.get('definitive_outcome')) != str(glosId):
                update_fields.append("definitive_outcome = %s")
                params.append(glosId)

            if existing.get('zOg') != selectedType:
                update_fields.append("zOg = %s")
                params.append(selectedType)

            # Only execute update if there are actual changes
            if update_fields:
                params.extend([date_obj, time_str, glosId])
                update_sql = f"UPDATE matched_transcriptions SET {', '.join(update_fields)} WHERE date = %s AND time = %s AND m_transcription = %s"

                try:
                    cursor.execute(update_sql, params)
                    conn.commit()
                    print(f"Updated record for {date_obj}, {time_str}, {glosId} ({len(update_fields)} fields changed)")
                except mysql.connector.Error as err:
                    print(f"Error updating record: {err}")
            else:
                print(f"No changes needed for {date_obj}, {time_str}, {glosId}")
        
        else:
            # Insert new record (set added=1 for new records)
            insert_sql = """INSERT INTO matched_transcriptions 
                           (m_file, l_file, r_file, a_file, b_file,
                            m_transcription, l_transcription, r_transcription, a_transcription, b_transcription,
                            added, definitive_outcome, zOg, time, date) 
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
            
            params = [
                cameras.get('m_file'),
                cameras.get('l_file'),
                cameras.get('r_file'),
                cameras.get('a_file'),
                cameras.get('b_file'),
                glosId, glosId, glosId, glosId, glosId,
                "1", glosId, selectedType, time_str, date_obj
            ]
            
            try:
                cursor.execute(insert_sql, params)
                conn.commit()
                print(f"Inserted new record for {date_obj}, {time_str}, {glosId}")
            except mysql.connector.Error as err:
                print(f"Error inserting record: {err}")

def get_db_connection():
    """Establish database connection."""
    db_config = {
        'host': 'localhost',
        'user': 'user',
        'password': os.environ.get('DB_PASS', ''),
        'database': 'admin_gebarenoverleg'
    }
    
    try:
        connection = mysql.connector.connect(**db_config)
        if connection.is_connected():
            print("Successfully connected to the database.")
            return connection
    except mysql.connector.Error as err:
        print(f"Error connecting to MySQL: {err}")
    return None

def main():
    """Main execution function."""
    # Process MP4 files and extract QR data
    video_data = process_mp4_files()
    
    if not video_data:
        print("No video data found.")
        return
    
    # Group videos by transcription
    groups = group_videos_by_transcription(video_data)
    
    # Connect to database and update records
    conn = get_db_connection()
    if not conn:
        print("Failed to connect to the database. Exiting.")
        return
    
    cursor = conn.cursor(dictionary=True)
    
    try:
        update_database(groups, cursor, conn)
    finally:
        cursor.close()
        conn.close()
    
    # Clean up PNG files
    for filename in os.listdir("/web/qr"):
        if filename.upper().endswith('.PNG'):
            os.remove(os.path.join("/web/qr", filename))
            print(f"Removed {filename}")

if __name__ == "__main__":
    script_name = os.path.basename(__file__)
    script_name = os.path.splitext(script_name)[0]
    if is_already_running(script_name):
        print(f"Another instance of {script_name} is already running. Exiting.")
        sys.exit(0)
    main()

