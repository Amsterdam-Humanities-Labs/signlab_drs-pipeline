#!/usr/bin/env python3
"""
Keyboard Activity Monitor for macOS
This script monitors ONLY keyboard input (ignoring mouse) and writes
the timestamp of the last keyboard activity to a file.
This allows checking keyboard-only idle time even when mouse automation is running.
"""

import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Ensure output is flushed immediately
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# File to store last keyboard activity timestamp
TIMESTAMP_FILE = "/Users/signlab/drs/last_keyboard_activity.txt"

def update_timestamp():
    """Update the timestamp file with current time"""
    try:
        with open(TIMESTAMP_FILE, 'w') as f:
            f.write(str(time.time()))
    except Exception as e:
        print(f"Error updating timestamp: {e}")

def on_press(key):
    """Callback when a key is pressed"""
    update_timestamp()

def on_release(key):
    """Callback when a key is released"""
    # We only need to track on press, but keeping this for completeness
    pass

if __name__ == "__main__":
    print("=" * 60)
    print("Keyboard Activity Monitor")
    print("=" * 60)
    print(f"Monitoring keyboard input...")
    print(f"Writing timestamps to: {TIMESTAMP_FILE}")
    print("Press Ctrl+C to stop")
    print("=" * 60)
    print()

    # Initialize timestamp file with current time
    update_timestamp()

    try:
        # Try to import pynput
        from pynput import keyboard

        print("✅ Using pynput library for keyboard monitoring")
        print("Monitoring keyboard activity (ignoring mouse)...")
        print()

        # Start listening for keyboard events
        with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
            listener.join()

    except ImportError:
        print("❌ pynput library not found!")
        print()
        print("To install pynput, run:")
        print("  pip3 install pynput")
        print()
        print("Or if you have system integrity protection:")
        print("  pip3 install --break-system-packages pynput")
        print()
        print("After installation, run this script again.")
        sys.exit(1)

    except KeyboardInterrupt:
        print()
        print("Keyboard monitor stopped.")
        sys.exit(0)

    except Exception as e:
        print(f"Error: {e}")
        print()
        print("Note: This script may require accessibility permissions.")
        print("Go to: System Preferences > Security & Privacy > Privacy > Accessibility")
        print("and grant permission to Terminal or your Python executable.")
        sys.exit(1)
