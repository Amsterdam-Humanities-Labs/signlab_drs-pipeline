#!/usr/bin/env python3
"""
Mouse movement script to prevent macOS screen lock.
Keeps the system active by periodically moving the mouse cursor.
"""

import pyautogui
import time
import random
import logging
import signal
import sys
import os
import argparse
import json
from datetime import datetime

# Configuration defaults
DEFAULT_INTERVAL = 60  # seconds between movements
DEFAULT_MOVEMENT_RANGE = 50  # pixels from center
DEFAULT_MOVEMENT_DURATION = 0.1  # seconds for mouse movement
CONFIG_FILE = "mouse_config.json"
LOG_DIR = "logs"

# Global flag for graceful shutdown
shutdown_requested = False

# Disable fail-safe to prevent corner trigger
pyautogui.FAILSAFE = False


def setup_logging():
    """Setup logging with rotation support."""
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR)
    
    log_file = os.path.join(LOG_DIR, "mouse.log")
    
    # Check if log file needs rotation (>10MB)
    if os.path.exists(log_file) and os.path.getsize(log_file) > 10 * 1024 * 1024:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        os.rename(log_file, f"{log_file}.{timestamp}")
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout)
        ]
    )
    return logging.getLogger(__name__)


def load_config():
    """Load configuration from file or environment variables."""
    config = {
        'interval': DEFAULT_INTERVAL,
        'movement_range': DEFAULT_MOVEMENT_RANGE,
        'movement_duration': DEFAULT_MOVEMENT_DURATION
    }
    
    # Try to load from config file
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                file_config = json.load(f)
                config.update(file_config)
                logger.info(f"Loaded configuration from {CONFIG_FILE}")
        except Exception as e:
            logger.warning(f"Failed to load config file: {e}")
    
    # Environment variables override file config
    if 'MOUSE_INTERVAL' in os.environ:
        config['interval'] = int(os.environ['MOUSE_INTERVAL'])
    if 'MOUSE_MOVEMENT_RANGE' in os.environ:
        config['movement_range'] = int(os.environ['MOUSE_MOVEMENT_RANGE'])
    if 'MOUSE_MOVEMENT_DURATION' in os.environ:
        config['movement_duration'] = float(os.environ['MOUSE_MOVEMENT_DURATION'])
    
    return config


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    global shutdown_requested
    logger.info(f"Received signal {signum}, initiating graceful shutdown...")
    shutdown_requested = True


def get_screen_center():
    """Get screen center with error handling."""
    try:
        screen_width, screen_height = pyautogui.size()
        return screen_width // 2, screen_height // 2
    except Exception as e:
        logger.error(f"Failed to get screen dimensions: {e}")
        # Return reasonable defaults
        return 1920 // 2, 1080 // 2


def move_mouse_safely(x, y, duration):
    """Move mouse with error handling and retry logic."""
    max_retries = 3
    retry_delay = 1
    
    for attempt in range(max_retries):
        try:
            pyautogui.moveTo(x, y, duration=duration)
            return True
        except Exception as e:
            logger.warning(f"Mouse movement error on attempt {attempt + 1}: {e}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                retry_delay *= 2
    
    return False


def perform_health_check():
    """Perform basic health check."""
    try:
        # Try to get current mouse position
        x, y = pyautogui.position()
        logger.debug(f"Health check passed - current position: ({x}, {y})")
        return True
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return False


def main():
    """Main execution loop."""
    global logger
    
    # Parse command-line arguments
    parser = argparse.ArgumentParser(description='Mouse movement script to prevent screen lock')
    parser.add_argument('--interval', type=int, help='Seconds between movements')
    parser.add_argument('--range', type=int, help='Movement range in pixels')
    parser.add_argument('--duration', type=float, help='Movement duration in seconds')
    parser.add_argument('--config', help='Path to config file')
    args = parser.parse_args()
    
    # Setup logging
    logger = setup_logging()
    logger.info("Mouse movement script starting...")
    
    # Setup signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    # Load configuration
    if args.config:
        global CONFIG_FILE
        CONFIG_FILE = args.config
    
    config = load_config()
    
    # Override config with command-line arguments
    if args.interval:
        config['interval'] = args.interval
    if args.range:
        config['movement_range'] = args.range
    if args.duration:
        config['movement_duration'] = args.duration
    
    logger.info(f"Configuration: interval={config['interval']}s, "
                f"range={config['movement_range']}px, "
                f"duration={config['movement_duration']}s")
    
    # Get screen center
    center_x, center_y = get_screen_center()
    logger.info(f"Screen center: ({center_x}, {center_y})")
    
    # Initial delay and move to center
    time.sleep(1)
    if not move_mouse_safely(center_x, center_y, config['movement_duration']):
        logger.error("Failed initial mouse movement, but continuing...")
    
    # Main loop
    movement_count = 0
    last_health_check = time.time()
    health_check_interval = 300  # 5 minutes
    consecutive_failures = 0
    max_consecutive_failures = 5
    
    try:
        while not shutdown_requested:
            # Perform periodic health check
            if time.time() - last_health_check > health_check_interval:
                if perform_health_check():
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                    if consecutive_failures >= max_consecutive_failures:
                        logger.error(f"Too many consecutive health check failures ({consecutive_failures})")
                        sys.exit(1)
                last_health_check = time.time()
            
            # Generate random offset
            offset_x = random.randint(-config['movement_range'], config['movement_range'])
            offset_y = random.randint(-config['movement_range'], config['movement_range'])
            
            # Calculate new position
            new_x = center_x + offset_x
            new_y = center_y + offset_y
            
            # Move mouse
            if move_mouse_safely(new_x, new_y, config['movement_duration']):
                movement_count += 1
                if movement_count % 10 == 0:  # Log every 10 movements
                    logger.info(f"Completed {movement_count} movements")
            else:
                logger.warning("Failed to move mouse, will retry on next interval")
            
            # Wait for next movement
            for _ in range(config['interval']):
                if shutdown_requested:
                    break
                time.sleep(1)
                
    except Exception as e:
        logger.error(f"Unexpected error in main loop: {e}", exc_info=True)
        sys.exit(1)
    finally:
        logger.info(f"Mouse movement script shutting down after {movement_count} movements")


if __name__ == "__main__":
    main()