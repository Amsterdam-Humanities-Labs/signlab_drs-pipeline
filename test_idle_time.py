#!/usr/bin/env python3
"""
Test script for keyboard idle time detection on macOS.
This script tests the check_user_idle_time() function to verify
it correctly detects how long since the last KEYBOARD activity.
NOTE: Requires keyboard_monitor.py to be running in the background.
"""

import os
import time
from datetime import datetime

def check_user_idle_time():
    """
    Check how long the user has been idle (KEYBOARD ONLY) on macOS.
    This reads from a timestamp file written by keyboard_monitor.py
    Returns the idle time in seconds, or None if unable to determine.
    """
    timestamp_file = "/Users/signlab/drs/last_keyboard_activity.txt"

    try:
        # Check if timestamp file exists
        if not os.path.exists(timestamp_file):
            print(f"❌ Keyboard activity timestamp file not found: {timestamp_file}")
            print("   Make sure keyboard_monitor.py is running in the background.")
            return None

        # Read the last keyboard activity timestamp
        with open(timestamp_file, 'r') as f:
            last_activity_timestamp = float(f.read().strip())

        # Calculate idle time
        current_time = time.time()
        idle_seconds = current_time - last_activity_timestamp

        return idle_seconds

    except ValueError as e:
        print(f"❌ Error parsing timestamp file: {e}")
        return None
    except Exception as e:
        print(f"❌ Error checking keyboard idle time: {e}")
        return None

def is_user_idle_long_enough(required_idle_seconds=3600):
    """
    Check if the user has been idle (KEYBOARD ONLY) for at least the required time.
    Default is 3600 seconds (1 hour).
    Returns True if user has been idle long enough, False otherwise.
    NOTE: Requires keyboard_monitor.py to be running in the background.
    """
    idle_time = check_user_idle_time()

    if idle_time is None:
        print("WARNING: Could not determine keyboard idle time.")
        return False

    idle_minutes = int(idle_time / 60)
    required_minutes = int(required_idle_seconds / 60)

    if idle_time >= required_idle_seconds:
        print(f"✅ Keyboard has been idle for {idle_minutes} minutes (required: {required_minutes} minutes). OK to proceed.")
        return True
    else:
        print(f"❌ Keyboard activity detected. Idle time: {idle_minutes} minutes (required: {required_minutes} minutes).")
        return False

def format_time(seconds):
    """Format seconds into a human-readable string"""
    if seconds is None:
        return "Unknown"

    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)

    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    elif minutes > 0:
        return f"{minutes}m {secs}s"
    else:
        return f"{secs}s"

if __name__ == "__main__":
    print("=" * 60)
    print("Keyboard Idle Time Test Script (KEYBOARD ONLY)")
    print("=" * 60)
    print("NOTE: Mouse activity is IGNORED")
    print("      Requires keyboard_monitor.py running in background")
    print("=" * 60)
    print()

    # Test 1: Get raw idle time
    print("Test 1: Raw Keyboard Idle Time Detection")
    print("-" * 60)
    idle_seconds = check_user_idle_time()

    if idle_seconds is not None:
        print(f"Current idle time: {format_time(idle_seconds)}")
        print(f"Idle time (raw): {idle_seconds:.2f} seconds")
    else:
        print("❌ Failed to detect idle time")

    print()

    # Test 2: Check if idle for 1 minute
    print("Test 2: Check if idle for 1 minute (60 seconds)")
    print("-" * 60)
    is_user_idle_long_enough(required_idle_seconds=60)

    print()

    # Test 3: Check if idle for 5 minutes
    print("Test 3: Check if idle for 5 minutes (300 seconds)")
    print("-" * 60)
    is_user_idle_long_enough(required_idle_seconds=300)

    print()

    # Test 4: Check if idle for 1 hour
    print("Test 4: Check if idle for 1 hour (3600 seconds)")
    print("-" * 60)
    is_user_idle_long_enough(required_idle_seconds=3600)

    print()
    print("=" * 60)
    print("Test complete!")
    print("=" * 60)
