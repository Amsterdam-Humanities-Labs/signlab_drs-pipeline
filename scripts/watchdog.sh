#!/bin/bash
# Watchdog script for startupScript.py

SCRIPT_PATH="/Users/signlab/drs/startupScript.py"
PID_FILE="/Users/signlab/drs/startup.pid"
LOG_FILE="/Users/signlab/drs/watchdog.log"

log_message() {
    echo "$(date): $1" >> "$LOG_FILE"
}

while true; do
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ! kill -0 "$PID" 2>/dev/null; then
            log_message "StartupScript (PID: $PID) is not running. Restarting..."
            rm -f "$PID_FILE"
            nohup /usr/bin/python3 "$SCRIPT_PATH" > /dev/null 2>&1 &
            echo $! > "$PID_FILE"
            log_message "StartupScript restarted with PID: $(cat $PID_FILE)"
        fi
    else
        log_message "PID file not found. Starting startupScript..."
        nohup /usr/bin/python3 "$SCRIPT_PATH" > /dev/null 2>&1 &
        echo $! > "$PID_FILE"
        log_message "StartupScript started with PID: $(cat $PID_FILE)"
    fi
    sleep 30
done
