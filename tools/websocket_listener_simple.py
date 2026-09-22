#!/usr/bin/env python3
"""
Simple WebSocket Listener Script (using websocket-client)
Connects to localhost:8081 and logs all messages to both terminal and log file
"""

import websocket
import json
import logging
import logging.handlers
import sys
import time
import threading
from datetime import datetime

# Configure logging to both file and console
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        # 5 MB x 5, the same policy as setup_rotating_logger in the heartbeat client.
        logging.handlers.RotatingFileHandler(
            'websocket_listener.log', maxBytes=5 * 1024 * 1024, backupCount=5),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

class WebSocketListener:
    def __init__(self, uri="ws://localhost:8081"):
        self.uri = uri
        self.ws = None
        self.running = False

    def on_message(self, ws, message):
        """Handle incoming WebSocket messages"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        
        try:
            # Try to parse as JSON for pretty printing
            parsed_message = json.loads(message)
            formatted_message = json.dumps(parsed_message, indent=2)
            logger.info(f"[{timestamp}] Received JSON message:\n{formatted_message}")
        except json.JSONDecodeError:
            # If not JSON, log as plain text
            logger.info(f"[{timestamp}] Received message: {message}")

    def on_error(self, ws, error):
        """Handle WebSocket errors"""
        logger.error(f"WebSocket error: {error}")

    def on_close(self, ws, close_status_code, close_msg):
        """Handle WebSocket connection close"""
        logger.warning(f"WebSocket connection closed: {close_status_code} - {close_msg}")
        self.running = False

    def on_open(self, ws):
        """Handle WebSocket connection open"""
        logger.info("WebSocket connection established")
        self.running = True

    def run_with_reconnect(self, retry_delay=5):
        """Run the listener with automatic reconnection"""
        logger.info(f"Starting WebSocket Listener for {self.uri}")
        logger.info("Press Ctrl+C to stop")
        
        while True:
            try:
                logger.info(f"Attempting to connect to {self.uri}")
                
                # Enable tracing for debugging (optional)
                # websocket.enableTrace(True)
                
                self.ws = websocket.WebSocketApp(
                    self.uri,
                    on_message=self.on_message,
                    on_error=self.on_error,
                    on_close=self.on_close,
                    on_open=self.on_open
                )
                
                # Run forever with automatic reconnection
                self.ws.run_forever(reconnect=retry_delay)
                
                if not self.running:
                    logger.info(f"Connection lost. Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                
            except KeyboardInterrupt:
                logger.info("Received keyboard interrupt. Shutting down...")
                if self.ws:
                    self.ws.close()
                break
            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                logger.info(f"Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)

def main():
    """Main function"""
    listener = WebSocketListener()
    
    try:
        listener.run_with_reconnect()
    except KeyboardInterrupt:
        logger.info("Shutting down gracefully...")
    finally:
        logger.info("WebSocket listener stopped")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nExiting...")