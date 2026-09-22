#!/usr/bin/env python3
"""
WebSocket Listener Script
Connects to localhost:8081 and logs all messages to both terminal and log file
"""

import asyncio
import websockets
import json
import logging
import logging.handlers
from datetime import datetime
import sys

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
        self.websocket = None
        self.running = False

    async def connect(self):
        """Establish WebSocket connection"""
        try:
            logger.info(f"Attempting to connect to {self.uri}")
            self.websocket = await websockets.connect(self.uri)
            logger.info("Successfully connected to WebSocket server")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to WebSocket server: {e}")
            return False

    async def listen(self):
        """Listen for messages from WebSocket server"""
        self.running = True
        try:
            async for message in self.websocket:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                
                # Try to parse as JSON for pretty printing
                try:
                    parsed_message = json.loads(message)
                    formatted_message = json.dumps(parsed_message, indent=2)
                    logger.info(f"[{timestamp}] Received JSON message:\n{formatted_message}")
                except json.JSONDecodeError:
                    # If not JSON, log as plain text
                    logger.info(f"[{timestamp}] Received message: {message}")
                    
        except websockets.exceptions.ConnectionClosed:
            logger.warning("WebSocket connection closed")
        except Exception as e:
            logger.error(f"Error while listening: {e}")
        finally:
            self.running = False

    async def disconnect(self):
        """Close WebSocket connection"""
        if self.websocket:
            await self.websocket.close()
            logger.info("WebSocket connection closed")

    async def run_with_reconnect(self, max_retries=None, retry_delay=5):
        """Run the listener with automatic reconnection"""
        retry_count = 0
        
        while True:
            try:
                if await self.connect():
                    retry_count = 0  # Reset retry count on successful connection
                    await self.listen()
                else:
                    retry_count += 1
                    if max_retries is not None and retry_count >= max_retries:
                        logger.error(f"Max retries ({max_retries}) reached. Exiting.")
                        break
                    
                    logger.info(f"Retrying connection in {retry_delay} seconds... (attempt {retry_count})")
                    await asyncio.sleep(retry_delay)
                    
            except KeyboardInterrupt:
                logger.info("Received keyboard interrupt. Shutting down...")
                break
            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                retry_count += 1
                if max_retries is not None and retry_count >= max_retries:
                    logger.error(f"Max retries ({max_retries}) reached. Exiting.")
                    break
                    
                logger.info(f"Retrying in {retry_delay} seconds... (attempt {retry_count})")
                await asyncio.sleep(retry_delay)
        
        await self.disconnect()

async def main():
    """Main function"""
    logger.info("Starting WebSocket Listener")
    logger.info("Press Ctrl+C to stop")
    
    listener = WebSocketListener()
    
    try:
        # Run with automatic reconnection (no max retries, infinite reconnection)
        await listener.run_with_reconnect()
    except KeyboardInterrupt:
        logger.info("Shutting down gracefully...")
    finally:
        logger.info("WebSocket listener stopped")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nExiting...")