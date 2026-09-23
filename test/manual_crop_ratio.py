"""Test script: pick 10 random post_noncropped videos, run crop, verify 1.15 ratio.
Does NOT upload - only checks dimensions locally."""
import os
import random
import subprocess
import sys
import uuid
import shutil

sys.path.insert(0, '/Users/signlab/drs/services')
sys.path.insert(0, '/Users/signlab/drs/shared')
from crop import extract_frames, process_frames, write_video, reencode_with_ffmpeg

BASE_DIR = '/Users/signlab/signCollect/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles'
TEMP_DIR = '/Users/signlab/drs/temp/test_ratio'
OUTPUT_DIR = '/Users/signlab/drs/temp/test_ratio_output'

def find_all_post_noncropped_videos():
    """Collect all video files from post_noncropped dirs"""
    videos = []
    for subdir in os.listdir(BASE_DIR):
        input_folder = os.path.join(BASE_DIR, subdir, 'post_noncropped')
        if not os.path.isdir(input_folder):
            continue
        for f in os.listdir(input_folder):
            if f.lower().endswith(('.mp4', '.mov')):
                videos.append(os.path.join(input_folder, f))
    return videos

def get_dimensions(filepath):
    result = subprocess.run(
        ['/opt/homebrew/bin/ffprobe', '-v', 'error', '-select_streams', 'v:0',
         '-show_entries', 'stream=width,height', '-of', 'csv=p=0', filepath],
        capture_output=True, text=True
    )
    if result.returncode == 0 and result.stdout.strip():
        w, h = result.stdout.strip().split(',')
        return int(w), int(h)
    return None, None

def process_single(input_file, output_file):
    """Process a video without uploading — just crop + reencode"""
    import cv2
    task_dir = os.path.join(TEMP_DIR, f"task_{uuid.uuid4().hex}")
    os.makedirs(task_dir, exist_ok=True)

    try:
        frames_dir = os.path.join(task_dir, "frames")
        os.makedirs(frames_dir, exist_ok=True)
        frame_paths, _ = extract_frames(input_file, frames_dir)

        if not frame_paths:
            return False

        cap = cv2.VideoCapture(input_file)
        fps = cap.get(cv2.CAP_PROP_FPS)
        cap.release()

        processed_dir = os.path.join(task_dir, "processed")
        os.makedirs(processed_dir, exist_ok=True)

        processed_paths, _, head, waist, margin, _ = process_frames(frame_paths, processed_dir)

        write_video(processed_paths, output_file, fps)

        h264_file = reencode_with_ffmpeg(output_file)
        return h264_file is not None
    finally:
        if os.path.exists(task_dir):
            shutil.rmtree(task_dir)

def main():
    all_videos = find_all_post_noncropped_videos()
    print(f"Found {len(all_videos)} total videos in post_noncropped dirs")

    sample = random.sample(all_videos, min(10, len(all_videos)))
    print(f"Testing {len(sample)} random videos (NO uploads):\n")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    results = []

    for i, input_file in enumerate(sample):
        basename = os.path.basename(input_file)
        print(f"[{i+1}/10] Processing {basename}...")

        output_file = os.path.join(OUTPUT_DIR, basename)
        h264_file = os.path.splitext(output_file)[0] + '_h264' + os.path.splitext(output_file)[1]

        try:
            success = process_single(input_file, output_file)

            check_file = h264_file if os.path.exists(h264_file) else output_file
            if os.path.exists(check_file):
                w, h = get_dimensions(check_file)
                if w and h:
                    ratio = round(w / h, 4)
                    status = "PASS" if abs(ratio - 1.15) < 0.02 else "FAIL"
                    results.append((basename, w, h, ratio, status))
                    print(f"  -> {w}x{h} ratio={ratio} [{status}]")
                else:
                    results.append((basename, None, None, None, "NO_DIMS"))
                    print(f"  -> Could not read dimensions")
            else:
                results.append((basename, None, None, None, "NO_OUTPUT"))
                print(f"  -> No output file produced")
        except Exception as e:
            results.append((basename, None, None, None, f"ERROR"))
            print(f"  -> Error: {e}")
        finally:
            # Clean up output files
            for f in [output_file, h264_file]:
                if os.path.exists(f):
                    os.remove(f)

    print("\n" + "="*70)
    print(f"{'File':<35} {'W':>6} {'H':>6} {'Ratio':>8} {'Status':>8}")
    print("-"*70)
    for name, w, h, ratio, status in results:
        w_str = str(w) if w else '-'
        h_str = str(h) if h else '-'
        r_str = str(ratio) if ratio else '-'
        print(f"{name:<35} {w_str:>6} {h_str:>6} {r_str:>8} {status:>8}")

    passes = sum(1 for r in results if r[4] == "PASS")
    print(f"\nResult: {passes}/{len(results)} passed (ratio within 0.02 of 1.15)")

    # Cleanup
    if os.path.exists(OUTPUT_DIR):
        shutil.rmtree(OUTPUT_DIR)

if __name__ == '__main__':
    main()
