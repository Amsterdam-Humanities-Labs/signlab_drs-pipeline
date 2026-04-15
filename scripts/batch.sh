#!/bin/bash
# Watchdog script for batch.py
# Restarts batch.py automatically after it exits (success or crash)
# This ensures all file descriptors are released between runs

cd /Users/signlab/drs

# Trap Ctrl+C - only kill batch.py, not the watchdog
trap 'echo "Ctrl+C pressed - batch.py will restart after current operation..."' SIGINT

while true; do
    echo "========================================"
    echo "Starting batch.py at $(date)"
    echo "========================================"

    # Run batch.py (runs once and exits)
    /usr/bin/python3 services/batch.py

    EXIT_CODE=$?
    echo "batch.py exited with code $EXIT_CODE at $(date)"

    # Kill any lingering DaVinci Resolve processes
    pkill -9 -f 'DaVinci Resolve' 2>/dev/null

    echo "Sleeping 60 seconds before restart..."
    sleep 60
done
