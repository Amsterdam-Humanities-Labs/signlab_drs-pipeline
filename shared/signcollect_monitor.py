"""
SignCollect Client Monitor Library

A reusable Python library for sending heartbeat/monitoring data to the SignCollect server.
Allows services to register themselves and send periodic heartbeats with optional status information.
"""

import requests
import socket
import json
from datetime import datetime
from typing import Optional, Dict, Any
from server_config import server_url

DEFAULT_API_URL = server_url("client_monitor_api/api.php")


class SignCollectMonitor:
    """
    Client monitor for SignCollect services.

    This class provides methods to register clients and send heartbeats
    to the SignCollect monitoring system.
    """

    def __init__(
        self,
        client_id: str,
        client_name: str,
        description: str = "",
        heartbeat_interval: int = 3600,
        api_url: str = DEFAULT_API_URL
    ):
        """
        Initialize the SignCollect monitor client.

        Args:
            client_id: Unique identifier for this client (e.g., 'drs-batch-processor')
            client_name: Human-readable name for this client (e.g., 'DRS Batch Processor')
            description: Optional description of what this client does
            heartbeat_interval: Expected interval between heartbeats in seconds (default: 3600)
            api_url: API endpoint URL (default: SIGNCOLLECT_URL, see server_config.py)
        """
        self.client_id = client_id
        self.client_name = client_name
        self.description = description
        self.heartbeat_interval = heartbeat_interval
        self.api_url = api_url

    def _get_hostname(self) -> str:
        """Get the current machine's hostname."""
        try:
            return socket.gethostname()
        except Exception:
            return "unknown"

    def _make_request(self, action: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Make a POST request to the API.

        Args:
            action: The API action to call
            data: The POST data to send

        Returns:
            dict: The JSON response from the API or error dict
        """
        try:
            response = requests.post(
                self.api_url,
                params={'action': action},
                json=data,
                timeout=30
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error making request to monitor API: {e}")
            return {"error": str(e), "success": False}
        except json.JSONDecodeError as e:
            print(f"Error decoding JSON response: {e}")
            return {"error": f"Invalid JSON response: {e}", "success": False}

    def register(self, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Register this client with the monitoring system.

        Args:
            metadata: Optional additional metadata to include

        Returns:
            dict: API response with success status
        """
        data = {
            "client_id": self.client_id,
            "client_name": self.client_name,
            "description": self.description,
            "heartbeat_interval": self.heartbeat_interval,
            "hostname": self._get_hostname()
        }

        if metadata:
            data["metadata"] = json.dumps(metadata)

        result = self._make_request("register", data)

        if result.get("success"):
            print(f"Client '{self.client_name}' registered successfully")
        else:
            error_msg = result.get("error", result.get("message", "Unknown error"))
            print(f"Failed to register client '{self.client_name}': {error_msg}")

        return result

    def send_heartbeat(self, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Send a heartbeat to update the last_seen timestamp.

        Args:
            metadata: Optional additional metadata to include

        Returns:
            dict: API response with success status
        """
        data = {
            "client_id": self.client_id
        }

        if metadata:
            data["metadata"] = json.dumps(metadata)

        result = self._make_request("heartbeat", data)

        if result.get("success"):
            print(f"Heartbeat sent for '{self.client_name}'")
        else:
            error_msg = result.get("error", result.get("message", "Unknown error"))
            print(f"Failed to send heartbeat for '{self.client_name}': {error_msg}")

        return result

    def send_heartbeat_with_stats(
        self,
        status: str,
        message: str,
        stats: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Send a heartbeat with status information and statistics.

        This method sends a regular heartbeat with the stats, status, and message
        included as metadata.

        Args:
            status: Status string (e.g., 'success', 'warning', 'error')
            message: Human-readable status message
            stats: Optional dictionary of statistics/metrics

        Returns:
            dict: API response with success status
        """
        # Build metadata with status info and stats
        metadata = {
            "status": status,
            "message": message,
            "timestamp": datetime.now().isoformat()
        }

        if stats:
            metadata["stats"] = stats

        # Use the regular heartbeat endpoint with metadata
        return self.send_heartbeat(metadata=metadata)


# Example usage and testing
if __name__ == '__main__':
    # Create a test monitor instance
    monitor = SignCollectMonitor(
        client_id='test-client',
        client_name='Test Client',
        description='A test client for verifying the monitoring system',
        heartbeat_interval=3600
    )

    # Test registration
    print("Testing registration...")
    reg_result = monitor.register(metadata={"version": "1.0.0"})
    print(f"Registration result: {reg_result}")
    print()

    # Test basic heartbeat
    print("Testing basic heartbeat...")
    hb_result = monitor.send_heartbeat()
    print(f"Heartbeat result: {hb_result}")
    print()

    # Test heartbeat with stats
    print("Testing heartbeat with stats...")
    stats_result = monitor.send_heartbeat_with_stats(
        status='success',
        message='Test completed successfully',
        stats={
            'processed_files': 42,
            'errors': 0,
            'uptime_seconds': 3600
        }
    )
    print(f"Heartbeat with stats result: {stats_result}")
