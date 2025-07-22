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
        # Scheduled restart configuration
        self.restart_times = [(6, 0), (20, 0)]  # 6:00 AM and 8:00 PM
        self.last_scheduled_restart = None  # Track last scheduled restart datetime
        
        self.services = [
            {
                'name': 'mouse',
                'command': ['/usr/bin/python3', '/Users/signlab/drs/mouse.py'],
                'cwd': '/Users/signlab/drs'
            },
            {
                'name': 'moveFiles',
                'command': ['/usr/bin/python3', '/Users/signlab/drs/moveFiles.py'],
                'cwd': '/Users/signlab/drs'
            },
            {
                'name': 'batch',
                'command': ['/usr/bin/python3', '/Users/signlab/drs/batch.py'],
                'cwd': '/Users/signlab/drs'
            },
            {
                'name': 'crop',
                'command': ['/usr/bin/python3', '/Users/signlab/drs/crop.py'],
                'cwd': '/Users/signlab/drs'
            },
            {
                'name': 'server',
                'command': ['/opt/homebrew/bin/node', '/Users/signlab/drs/startServer_beta.js'],
                'cwd': '/Users/signlab/drs'
            },
                {
                'name': 'convertFiles',
                'command': ['/usr/bin/python3', '/Users/signlab/drs/convertFiles.py'],
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
                    '--transfers', '1',
                    '--checkers', '4',
                    '--log-level', 'INFO',
                    '--allow-non-empty',
                    '--cache-dir', '/Volumes/cacheDisk/rclone',
                    '--vfs-cache-mode', 'full',
                    '--timeout', '0'
                ],
                'cwd': '/Users/signlab'
            },
            {
                   'name': 'listFiles',
                'command': ['/usr/bin/python3', '/Users/signlab/drs/listFiles.py'],
                'cwd': '/Users/signlab/drs'
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

    def check_mount_health(self) -> bool:
        """Check if rclone mount is healthy and accessible"""
        try:
            # First check if the mount point is actually mounted using system tools
            result = subprocess.run(['mount'], capture_output=True, text=True)
            mount_output = result.stdout
            
            # Check if our specific mount path appears in the mount list
            mount_found = False
            for line in mount_output.split('\n'):
                if self.mount_path in line and 'fuse' in line.lower():
                    mount_found = True
                    break
            
            if not mount_found:
                logging.warning(f"rclone mount not found in system mount table: {self.mount_path}")
                return False
            
            # Additionally verify the mount is responsive
            test_path = os.path.join(self.mount_path, "AIHR-FGW-TEST-SIGNLAB (Projectfolder)")
            if not os.path.exists(test_path):
                logging.warning(f"Expected directory not found in mount: {test_path}")
                return False
            
            # Try to access files to ensure mount is responsive
            try:
                contents = os.listdir(test_path)
                # Verify we can see some expected content (not just empty local directory)
                if not contents:
                    logging.warning(f"Mount directory appears empty, may be unmounted: {test_path}")
                    return False
                return True
            except (OSError, PermissionError) as e:
                logging.warning(f"Mount appears unresponsive: {e}")
                return False
                
        except Exception as e:
            logging.error(f"Error checking mount health: {e}")
            return False

    def restart_rclone_if_mount_failed(self):
        """Restart rclone service if mount is unhealthy"""
        rclone_service = None
        for service in self.services:
            if service['name'] == 'rclone':
                rclone_service = service
                break
        
        if rclone_service:
            logging.warning("Mount unhealthy - restarting rclone service")
            self.restart_service(rclone_service)
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
            # Special handling for rclone - umount first
            if service['name'] == 'rclone':
                try:
                    logging.info("Attempting to unmount /Users/signlab/signCollect before starting rclone")
                    subprocess.run(['umount', '-f', '/Users/signlab/signCollect'], 
                                 check=False, capture_output=True, timeout=30)
                except Exception as e:
                    logging.warning(f"Umount failed (this is normal if not mounted): {e}")
            
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
                    old_process.wait(timeout=10)
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
        
        # Clear the processes dictionary
        self.processes.clear()
        
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
                
                # Check mount health periodically
                if current_time - self.last_mount_check >= self.mount_check_interval:
                    if not self.check_mount_health():
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
                process.wait(timeout=10)
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
    
    watchdog_path = '/Users/signlab/drs/watchdog.sh'
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
        logging.info(f"Scheduled daily restart times: {restart_times_str}")
        
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