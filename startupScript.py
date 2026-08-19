#!/usr/bin/env python3

import subprocess
import time
import logging
import signal
import sys
import os
import traceback
from datetime import datetime
from typing import Dict, List, Optional

# Configure logging with rotation to prevent log files from growing too large
from logging.handlers import RotatingFileHandler

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler('/Users/signlab/drs/startup.log', maxBytes=10*1024*1024, backupCount=5),
        logging.StreamHandler(sys.stdout)
    ]
)

class ProcessMonitor:
    def __init__(self):
        self.processes: Dict[str, subprocess.Popen] = {}
        self.restart_counts: Dict[str, int] = {}
        self.max_restarts = 5  # Maximum restarts per service within restart_window
        self.restart_window = 300  # 5 minutes
        self.restart_timestamps: Dict[str, List[float]] = {}
        self.disabled_services: Dict[str, float] = {}  # Track when services were disabled
        self.cooldown_period = 900  # 15 minutes cooldown after max restarts
        self.log_dir = '/Users/signlab/drs/logs'
        self.log_files: Dict[str, str] = {}  # Track log file paths for each service
        self.mount_path = '/Users/signlab/signCollect'  # rclone mount path
        self.last_mount_check = 0  # Timestamp of last mount check
        self.mount_check_interval = 60  # Check mount every 60 seconds
        # rclone needs time to serve the mount after starting, especially when it
        # has a large vfs write-back backlog to flush. Without this grace period the
        # 60s marker check kills it mid-startup and it never mounts at all.
        self.rclone_started_at = 0  # Timestamp rclone was last (re)started
        self.rclone_grace_period = 1800  # Skip mount checks for 30 min after rclone start
        # Never kill an rclone that is still writing to its log: with a large
        # vfs write-back backlog it can be uploading unsaved data for a long time,
        # and restarting it mid-flight only makes the backlog worse.
        self.rclone_log = '/Users/signlab/drs/logs/rclone.log'
        self.rclone_active_window = 120  # Seconds of log silence before it counts as stalled
        # Scheduled restart configuration. Disabled: the 20:00 restart on
        # 2026-08-07 SIGKILLed rclone mid-flush, stranded the FUSE mount and took
        # the pipeline down. Services are still restarted on crash and rclone
        # still has its own mount health check, so nothing needs daily recycling.
        # Re-enable by putting (hour, minute) tuples back in this list.
        self.restart_times = []  # was [(6, 0), (20, 0)]
        # rclone is exempt from scheduled restarts. Stopping it mid-flight can
        # exceed the 10s wait, and the SIGKILL fallback leaves a dead FUSE mount
        # that ensure_unmounted() cannot clear - which blocks every remount after
        # it. It has its own health check, so it does not need periodic recycling.
        self.scheduled_restart_skip = {'rclone'}
        self.last_scheduled_restart = None  # Track last scheduled restart datetime
        
        self.services = [
            {
                'name': 'mouse',
                'command': ['/usr/bin/python3', '/Users/signlab/drs/services/mouse.py'],
                'cwd': '/Users/signlab/drs'
            },
            {
                'name': 'keyboardMonitor',
                'command': ['/usr/bin/python3', '/Users/signlab/drs/services/keyboard_monitor.py'],
                'cwd': '/Users/signlab/drs'
            },
            {
                'name': 'moveFiles',
                'command': ['/usr/bin/python3', '/Users/signlab/drs/services/moveFiles.py'],
                'cwd': '/Users/signlab/drs'
            },
            {
                'name': 'batch',
                'command': ['/usr/bin/python3', '/Users/signlab/drs/services/batch_queue.py'],
                'cwd': '/Users/signlab/drs'
            },
            {
                'name': 'crop',
                'command': ['/usr/bin/python3', '/Users/signlab/drs/services/crop.py'],
                'cwd': '/Users/signlab/drs'
            },
            # Disabled 2026-07-01: replaced by the Sony FX30 camera server
            # (fx30MultiRecord) which owns port 8080. Re-enable only if reverting.
            # {
            #     'name': 'server',
            #     'command': ['/opt/homebrew/bin/node', '/Users/signlab/drs/services/startServer_beta.js'],
            #     'cwd': '/Users/signlab/drs'
            # },
                {
                'name': 'convertFiles',
                'command': ['/usr/bin/python3', '/Users/signlab/drs/services/convertFiles.py'],
                'cwd': '/Users/signlab/drs'
            },
            {
                'name': 'rclone',
                'command': [
                    '/Users/signlab/rclone/rclone', 'mount', 'signcollect:', '/Users/signlab/signCollect',
                    '--allow-other',
                    '--vfs-cache-mode', 'full',
                    '--vfs-cache-max-size', '4000G',
                    '--vfs-write-back', '5s',
                    '--vfs-cache-poll-interval', '1m',
                    '--dir-cache-time', '2h',
                    '--transfers', '4',
                    '--checkers', '4',
                    '--log-level', 'INFO',
                    '--daemon-timeout', '60s',
                    '--cache-dir', '/Volumes/cacheDisk/rclone',
                    '--vfs-cache-mode', 'full',
                ],
                'cwd': '/Users/signlab'
            },
            {
                   'name': 'listFiles',
                'command': ['/usr/bin/python3', '/Users/signlab/drs/services/listFiles.py'],
                'cwd': '/Users/signlab/drs'
            },
            {
                'name': 'networkManager',
                'command': ['/usr/bin/python3', '-u', '/Users/signlab/drs/services/network_manager.py'],
                'cwd': '/Users/signlab/drs'
            },
            {
                'name': 'watchdog',
                'command': ['/bin/bash', '/Users/signlab/drs/scripts/watchdog.sh'],
                'cwd': '/Users/signlab/drs'
            },
            {
                'name': 'qrScanner',
                'command': ['/Users/signlab/drs/bin/python3', '/Users/signlab/drs/qr/qr_scanner_service.py'],
                'cwd': '/Users/signlab/drs/qr'
            }
        ]
        self.running = True
        
        # Create log directory and set up log files after services are defined
        self.setup_log_directory()

    def setup_log_directory(self):
        """Create log directory and set up log file paths for each service"""
        try:
            os.makedirs(self.log_dir, exist_ok=True)
            logging.info(f"Log directory created/verified at: {self.log_dir}")
            
            # Set up log file paths for each service
            for service in self.services:
                service_name = service['name']
                log_path = os.path.join(self.log_dir, f"{service_name}.log")
                self.log_files[service_name] = log_path
                
        except Exception as e:
            logging.error(f"Failed to create log directory {self.log_dir}: {e}")

    def network_ready(self, host="uva.data.surf.nl", timeout=10) -> bool:
        """True if the WebDAV host resolves - mounting without DNS just hangs."""
        try:
            result = subprocess.run(
                ['/usr/bin/python3', '-c',
                 f'import socket; socket.getaddrinfo("{host}", 443)'],
                capture_output=True, timeout=timeout)
            return result.returncode == 0
        except Exception:
            return False

    def ensure_unmounted(self, attempts=3) -> bool:
        """Force-unmount every stacked mount at mount_path. True if nothing is mounted.

        Restarting rclone used to mount over a dead mount (--allow-non-empty),
        stacking FUSE mounts until system daemons wedged in getattr calls.
        """
        for _ in range(attempts + 1):
            try:
                mounts = subprocess.run(['mount'], capture_output=True, text=True, timeout=15).stdout
            except Exception:
                return False
            if f' on {self.mount_path} ' not in mounts:
                return True
            subprocess.run(['umount', '-f', self.mount_path],
                           check=False, capture_output=True, timeout=30)
            time.sleep(2)
        return False

    def quarantine_stray_mountpoint(self) -> bool:
        """Move stray files out of an unmounted mount point so rclone can mount.

        Anything sitting in mount_path while nothing is mounted was written to the
        bare directory by a service that ran while the mount was down, so it exists
        only on local disk. rclone refuses to mount over a non-empty directory, and
        that deadlocks every later remount: the pipeline keeps writing there, so the
        directory never empties on its own and rclone crash-restarts forever.
        Move the content aside rather than deleting it - it is the only copy.

        Only call once ensure_unmounted() has confirmed nothing is mounted here,
        otherwise this would rename a live mount point.
        """
        try:
            entries = os.listdir(self.mount_path)
        except FileNotFoundError:
            os.makedirs(self.mount_path, exist_ok=True)
            return True
        except OSError as e:
            logging.error(f"Cannot inspect mount point {self.mount_path}: {e}")
            return False

        # Finder litter is not worth quarantining, but it still counts as non-empty.
        if '.DS_Store' in entries:
            try:
                os.remove(os.path.join(self.mount_path, '.DS_Store'))
                entries.remove('.DS_Store')
            except OSError:
                pass

        if not entries:
            return True

        quarantine = f"{self.mount_path}_stray_{time.strftime('%Y%m%d_%H%M%S')}"
        try:
            os.rename(self.mount_path, quarantine)
            os.makedirs(self.mount_path, exist_ok=True)
        except OSError as e:
            logging.error(f"Failed to quarantine stray mount point contents: {e}")
            return False

        logging.critical(
            f"Mount point {self.mount_path} was not empty while unmounted - moved "
            f"{len(entries)} entry/entries to {quarantine}. Those files were written "
            f"while the mount was down and exist ONLY there: copy them back onto the "
            f"mount once it is up, then delete the quarantine directory."
        )
        return True

    def check_mount_health(self) -> bool:
        """Check if rclone mount is healthy and accessible.

        Filesystem access happens in a child process with a timeout: a hung
        FUSE mount blocks stat() forever, which would wedge this monitor too.
        """
        marker_file = os.path.join(self.mount_path, "AIHR-FGW-TEST-SIGNLAB (Projectfolder)", "do_not_remove_for_rclone")
        try:
            result = subprocess.run(['ls', marker_file], capture_output=True, timeout=20)
            if result.returncode != 0:
                logging.warning(f"rclone marker file not found: {marker_file}")
                return False
            return True
        except subprocess.TimeoutExpired:
            logging.warning("Mount appears hung: marker check timed out")
            return False
        except Exception as e:
            logging.error(f"Error checking mount health: {e}")
            return False

    def rclone_making_progress(self) -> bool:
        """True if rclone has written to its log recently.

        A quiet marker check is not enough to declare rclone dead: while it drains
        a vfs write-back backlog it can hold unsaved data that only exists in the
        local cache, so killing it risks stalling those uploads indefinitely.
        """
        try:
            age = time.time() - os.path.getmtime(self.rclone_log)
        except OSError:
            return False
        return age < self.rclone_active_window

    def restart_rclone_if_mount_failed(self):
        """Restart rclone service if mount is unhealthy"""
        rclone_service = None
        for service in self.services:
            if service['name'] == 'rclone':
                rclone_service = service
                break
        
        if rclone_service:
            if not self.network_ready():
                logging.warning("Mount unhealthy but DNS is down - waiting for network instead of restarting rclone")
                return
            if self.rclone_making_progress():
                logging.warning("Mount unhealthy but rclone is still uploading - not restarting")
                return
            logging.warning("Mount unhealthy - restarting rclone service")
            self.restart_service(rclone_service)
            
            # Check if there's a recovery marker file and remove it after successful restart
            marker_file = '/Users/signlab/drs/mount_recovery_needed'
            if os.path.exists(marker_file):
                # Wait a bit for mount to stabilize
                time.sleep(10)
                
                # Verify mount is working before removing marker
                if self.check_mount_health():
                    try:
                        os.remove(marker_file)
                        logging.info(f"Removed mount recovery marker file: {marker_file}")
                    except Exception as e:
                        logging.error(f"Failed to remove marker file {marker_file}: {e}")
                else:
                    logging.warning("Mount still unhealthy after restart, keeping marker file")
        else:
            logging.error("Could not find rclone service configuration")

    def rotate_log_if_needed(self, log_path: str):
        """Rotate log file if it exceeds 10MB"""
        try:
            if os.path.exists(log_path):
                file_size = os.path.getsize(log_path)
                max_size = 10 * 1024 * 1024  # 10MB
                
                if file_size > max_size:
                    # Rotate the log file
                    backup_path = f"{log_path}.1"
                    if os.path.exists(backup_path):
                        os.remove(backup_path)
                    os.rename(log_path, backup_path)
                    logging.info(f"Rotated log file: {log_path} -> {backup_path}")
                    
        except Exception as e:
            logging.error(f"Failed to rotate log file {log_path}: {e}")

    def get_log_file_handle(self, service_name: str):
        """Get a file handler for a service's log file with rotation"""
        try:
            log_path = self.log_files.get(service_name)
            if not log_path:
                log_path = os.path.join(self.log_dir, f"{service_name}.log")
                self.log_files[service_name] = log_path
            
            # Check if rotation is needed before opening
            self.rotate_log_if_needed(log_path)
            
            # Add timestamp header when starting new log session
            timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
            with open(log_path, 'a') as header_file:
                header_file.write(f"\n--- Service {service_name} started at {timestamp} ---\n")
            
            # Open file handle for writing (subprocess will write to it)
            return open(log_path, 'a')
            
        except Exception as e:
            logging.error(f"Failed to create log file handle for {service_name}: {e}")
            return None

    def start_service(self, service: dict) -> Optional[subprocess.Popen]:
        """Start a single service"""
        try:
            # Special handling for rclone - never mount without DNS, and never
            # mount on top of a leftover (possibly dead) mount.
            if service['name'] == 'rclone':
                if not self.network_ready():
                    logging.warning("Not starting rclone: WebDAV host does not resolve yet - the mount check will retry")
                    return None
                logging.info(f"Ensuring {self.mount_path} is unmounted before starting rclone")
                if not self.ensure_unmounted():
                    logging.error("Refusing to start rclone: mount point still mounted after forced unmounts - a stacked mount would hang the system")
                    return None
                if not self.quarantine_stray_mountpoint():
                    logging.error("Refusing to start rclone: mount point is not empty and could not be cleared - rclone cannot mount over it")
                    return None
            
            logging.info(f"Starting service: {service['name']}")
            
            # Get log file handle for this service
            service_name = service['name']
            log_handle = self.get_log_file_handle(service_name)
            
            if log_handle:
                # Redirect stdout and stderr to the service's log file
                process = subprocess.Popen(
                    service['command'],
                    cwd=service['cwd'],
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,  # Combine stderr with stdout
                    preexec_fn=os.setsid,
                    universal_newlines=True,
                    bufsize=1  # Line buffering
                )
                
                # Store the log handle so we can close it later
                process._log_handle = log_handle
            else:
                # Fallback to no redirection if log file creation fails
                logging.warning(f"Could not create log file for {service_name}, proceeding without logging")
                process = subprocess.Popen(
                    service['command'],
                    cwd=service['cwd'],
                    preexec_fn=os.setsid
                )
            
            self.processes[service['name']] = process
            logging.info(f"Service {service['name']} started with PID {process.pid}")
            if service['name'] == 'rclone':
                self.rclone_started_at = time.time()
            return process
        except Exception as e:
            logging.error(f"Failed to start service {service['name']}: {e}")
            return None

    def check_process(self, name: str, process: subprocess.Popen) -> bool:
        """Check if a process is still running"""
        return process.poll() is None

    def should_restart_service(self, service_name: str) -> bool:
        """Check if service should be restarted based on restart limits"""
        now = time.time()
        
        # Check if service is in cooldown period
        if service_name in self.disabled_services:
            disabled_time = self.disabled_services[service_name]
            if now - disabled_time < self.cooldown_period:
                remaining_time = self.cooldown_period - (now - disabled_time)
                logging.info(f"Service {service_name} still in cooldown for {remaining_time:.0f} seconds")
                return False
            else:
                # Cooldown period has passed, re-enable the service
                logging.info(f"Cooldown period expired for {service_name}. Re-enabling restarts.")
                del self.disabled_services[service_name]
                self.restart_timestamps[service_name] = []  # Reset restart history
        
        if service_name not in self.restart_timestamps:
            self.restart_timestamps[service_name] = []
        
        # Remove old timestamps outside the window
        timestamps = self.restart_timestamps[service_name]
        timestamps[:] = [ts for ts in timestamps if now - ts < self.restart_window]
        
        # Check if we've exceeded restart limit
        if len(timestamps) >= self.max_restarts:
            logging.error(f"Service {service_name} has crashed {self.max_restarts} times in {self.restart_window}s. Disabling restarts for {self.cooldown_period/60:.0f} minutes.")
            self.disabled_services[service_name] = now
            return False
        
        return True

    def restart_service(self, service: dict):
        """Restart a crashed service with rate limiting"""
        name = service['name']
        
        if not self.should_restart_service(name):
            logging.error(f"Not restarting {name} - restart limit exceeded")
            return
        
        # Record restart attempt
        if name not in self.restart_timestamps:
            self.restart_timestamps[name] = []
        self.restart_timestamps[name].append(time.time())
        
        if name in self.processes:
            old_process = self.processes[name]
            if old_process.poll() is None:
                try:
                    # Close log file handle if it exists
                    if hasattr(old_process, '_log_handle') and old_process._log_handle:
                        try:
                            timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
                            old_process._log_handle.write(f"\n--- Service {name} crashed and restarting at {timestamp} ---\n")
                            old_process._log_handle.close()
                        except Exception as log_error:
                            logging.warning(f"Error closing log file for {name}: {log_error}")
                    
                    os.killpg(os.getpgid(old_process.pid), signal.SIGTERM)
                    # Same reasoning as stop_all_services: give rclone room to
                    # unmount rather than orphaning the mount point.
                    old_process.wait(timeout=90 if name in self.scheduled_restart_skip else 10)
                except:
                    pass
            del self.processes[name]
        
        logging.warning(f"Restarting crashed service: {name}")
        time.sleep(2)  # Brief delay before restart
        self.start_service(service)

    def check_scheduled_restart(self) -> bool:
        """Check if it's time for a scheduled restart"""
        now = datetime.now()
        current_time = (now.hour, now.minute)
        
        # Check if we've already done a scheduled restart today at this time
        if self.last_scheduled_restart:
            last_restart_date = self.last_scheduled_restart.date()
            last_restart_time = (self.last_scheduled_restart.hour, self.last_scheduled_restart.minute)
            
            # If we already restarted today at this scheduled time, skip
            if last_restart_date == now.date() and last_restart_time in self.restart_times:
                return False
        
        # Check if current time matches any scheduled restart time
        for restart_hour, restart_minute in self.restart_times:
            if current_time == (restart_hour, restart_minute):
                return True
        
        return False

    def perform_scheduled_restart(self):
        """Perform a scheduled restart of all services"""
        now = datetime.now()
        logging.info(f"=== Performing scheduled restart at {now.strftime('%Y-%m-%d %H:%M:%S')} ===")
        
        # Stop all services gracefully
        logging.info("Stopping all services for scheduled restart...")
        for name, process in list(self.processes.items()):
            if name in self.scheduled_restart_skip:
                logging.info(f"Leaving {name} running through scheduled restart")
                continue
            try:
                logging.info(f"Stopping service: {name}")

                # Close log file handle if it exists
                if hasattr(process, '_log_handle') and process._log_handle:
                    try:
                        timestamp = now.strftime('%Y-%m-%d %H:%M:%S')
                        process._log_handle.write(f"\n--- Service {name} stopped for scheduled restart at {timestamp} ---\n")
                        process._log_handle.close()
                    except Exception as log_error:
                        logging.warning(f"Error closing log file for {name}: {log_error}")
                
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                process.wait(timeout=10)
                logging.info(f"Service {name} stopped")
            except Exception as e:
                logging.warning(f"Error stopping {name}: {e}")
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except:
                    pass
        
        # Clear the processes dictionary, keeping any service we deliberately
        # left running so start_all_services does not launch a second copy.
        self.processes = {
            name: process for name, process in self.processes.items()
            if name in self.scheduled_restart_skip
        }
        
        # Clear restart history for all services (fresh start)
        self.restart_timestamps.clear()
        self.disabled_services.clear()
        
        # Wait a moment before restarting
        logging.info("Waiting 5 seconds before restarting services...")
        time.sleep(5)
        
        # Start all services again
        logging.info("Starting all services after scheduled restart...")
        self.start_all_services()
        
        # Update last scheduled restart time
        self.last_scheduled_restart = now
        logging.info(f"=== Scheduled restart completed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")

    def start_all_services(self):
        """Start all configured services"""
        logging.info("Starting all services...")
        for service in self.services:
            existing = self.processes.get(service['name'])
            if existing is not None and existing.poll() is None:
                logging.info(f"Service {service['name']} already running - not starting a second copy")
                continue
            self.start_service(service)
            time.sleep(1)  # Stagger startup

    def monitor_services(self):
        """Main monitoring loop with enhanced error handling"""
        logging.info("Starting service monitoring...")
        consecutive_errors = 0
        max_consecutive_errors = 10
        
        while self.running:
            try:
                current_time = time.time()
                
                # Check for scheduled restart
                if self.check_scheduled_restart():
                    self.perform_scheduled_restart()
                    # Continue monitoring after restart
                    continue
                
                # Check mount health periodically, but leave rclone alone while it
                # is still coming up - killing it mid-startup is how the mount ends
                # up permanently down.
                if current_time - self.last_mount_check >= self.mount_check_interval:
                    rclone_age = current_time - self.rclone_started_at
                    if self.rclone_started_at and rclone_age < self.rclone_grace_period:
                        logging.info(
                            f"Skipping mount check - rclone started {int(rclone_age)}s ago "
                            f"(grace period {self.rclone_grace_period}s)"
                        )
                    elif not self.check_mount_health():
                        self.restart_rclone_if_mount_failed()
                    self.last_mount_check = current_time
                
                for service in self.services:
                    name = service['name']
                    if name in self.processes:
                        process = self.processes[name]
                        if not self.check_process(name, process):
                            logging.error(f"Service {name} has crashed!")
                            self.restart_service(service)
                
                consecutive_errors = 0  # Reset error count on successful iteration
                time.sleep(5)  # Check every 5 seconds
                
            except Exception as e:
                consecutive_errors += 1
                logging.error(f"Error in monitoring loop (attempt {consecutive_errors}): {e}")
                logging.error(f"Traceback: {traceback.format_exc()}")
                
                if consecutive_errors >= max_consecutive_errors:
                    logging.critical("Too many consecutive monitoring errors. Exiting.")
                    break
                
                time.sleep(min(consecutive_errors * 2, 30))  # Exponential backoff, max 30s

    def stop_all_services(self):
        """Stop all running services"""
        logging.info("Stopping all services...")
        self.running = False
        
        for name, process in self.processes.items():
            try:
                logging.info(f"Stopping service: {name}")
                
                # Close log file handle if it exists
                if hasattr(process, '_log_handle') and process._log_handle:
                    try:
                        # Add termination timestamp to log
                        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
                        process._log_handle.write(f"\n--- Service {name} stopped at {timestamp} ---\n")
                        process._log_handle.close()
                    except Exception as log_error:
                        logging.warning(f"Error closing log file for {name}: {log_error}")

                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                # rclone needs long enough to unmount cleanly; SIGKILLing it mid
                # flush leaves a dead FUSE mount that blocks every later remount.
                process.wait(timeout=90 if name in self.scheduled_restart_skip else 10)
                logging.info(f"Service {name} stopped")
            except Exception as e:
                logging.warning(f"Error stopping {name}: {e}")
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except:
                    pass

def signal_handler(signum, frame):
    """Handle shutdown signals"""
    logging.info(f"Received shutdown signal {signum}")
    monitor.stop_all_services()
    sys.exit(0)

def create_watchdog_script():
    """Create a watchdog script to monitor the startup script itself"""
    watchdog_content = '''#!/bin/bash
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
'''
    
    watchdog_path = '/Users/signlab/drs/scripts/watchdog.sh'
    try:
        with open(watchdog_path, 'w') as f:
            f.write(watchdog_content)
        os.chmod(watchdog_path, 0o755)
        logging.info(f"Watchdog script created at {watchdog_path}")
    except Exception as e:
        logging.error(f"Failed to create watchdog script: {e}")

if __name__ == "__main__":
    # Create PID file for watchdog monitoring
    pid_file = '/Users/signlab/drs/startup.pid'
    try:
        with open(pid_file, 'w') as f:
            f.write(str(os.getpid()))
    except Exception as e:
        logging.error(f"Failed to create PID file: {e}")
    
    # Create watchdog script
    create_watchdog_script()
    
    # Set up signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Create and start monitor
    monitor = ProcessMonitor()
    
    try:
        # Log scheduled restart times
        restart_times_str = ", ".join([f"{h:02d}:{m:02d}" for h, m in monitor.restart_times])
        logging.info(f"Scheduled daily restart times: {restart_times_str or 'disabled'}")
        
        monitor.start_all_services()
        monitor.monitor_services()
    except KeyboardInterrupt:
        logging.info("Interrupted by user")
    except Exception as e:
        logging.critical(f"Critical error in startup script: {e}")
        logging.critical(f"Traceback: {traceback.format_exc()}")
    finally:
        try:
            monitor.stop_all_services()
            # Clean up PID file
            if os.path.exists(pid_file):
                os.remove(pid_file)
        except Exception as e:
            logging.error(f"Error during cleanup: {e}")