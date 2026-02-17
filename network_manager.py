#!/usr/bin/env python3
"""
Network Connection Manager for macOS
Monitors ethernet connection and manages Tailscale connectivity issues
"""

import subprocess
import time
import sys
import os
import socket
import argparse
from datetime import datetime
from signcollect_monitor import SignCollectMonitor  # Import the monitor client

# Initialize the monitor
monitor = SignCollectMonitor(
    client_id='drs-network-manager',
    client_name='DRS Network Manager',
    description='Ethernet and Tailscale connectivity monitor',
    heartbeat_interval=300
)

class NetworkManager:
    ADMIN_PASSWORD = "SignL@b2"
    
    def __init__(self, ethernet_interface="en0", check_interval=30):
        self.ethernet_interface = ethernet_interface
        self.check_interval = check_interval
        self.tailscale_restart_on_boot = True
        
    def run_command(self, command, shell=True, use_sudo_password=False):
        """Run a shell command and return output"""
        try:
            if use_sudo_password and command.strip().startswith('sudo'):
                # Insert -S flag after sudo and pipe password
                command = command.replace('sudo ', 'sudo -S ', 1)
                command = f"echo '{self.ADMIN_PASSWORD}' | {command}"
            
            result = subprocess.run(command, shell=shell, capture_output=True, text=True)
            return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
        except Exception as e:
            return False, "", str(e)
    
    def get_ethernet_interfaces(self):
        """Get all ethernet interfaces on the system"""
        success, output, _ = self.run_command("networksetup -listallhardwareports")
        if success:
            interfaces = []
            lines = output.split('\n')
            for i, line in enumerate(lines):
                if 'Ethernet' in line and i + 1 < len(lines):
                    device_line = lines[i + 1]
                    if 'Device:' in device_line:
                        interface = device_line.split('Device:')[1].strip()
                        interfaces.append(interface)
            return interfaces
        return []
    
    def check_ethernet_status(self):
        """Check if ethernet interface is active"""
        success, output, _ = self.run_command(f"ifconfig {self.ethernet_interface}")
        if success and 'status: active' in output:
            return True
        return False
    
    def check_internet_connection(self, host="8.8.8.8", port=53, timeout=3):
        """Check if we have internet connectivity"""
        try:
            socket.setdefaulttimeout(timeout)
            socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect((host, port))
            return True
        except socket.error:
            return False
    
    def toggle_ethernet(self, action="restart"):
        """Turn ethernet interface off/on or restart"""
        if action == "restart":
            print(f"[{datetime.now()}] Restarting ethernet interface {self.ethernet_interface}...")
        elif action == "on":
            print(f"[{datetime.now()}] Enabling ethernet interface {self.ethernet_interface}...")
        elif action == "off":
            print(f"[{datetime.now()}] Disabling ethernet interface {self.ethernet_interface}...")
        
        if action in ["restart", "off"]:
            # Turn off
            success, _, error = self.run_command(f"sudo ifconfig {self.ethernet_interface} down", use_sudo_password=True)
            if not success:
                print(f"Error turning off ethernet: {error}")
                return False
            time.sleep(2)
        
        if action in ["restart", "on"]:
            # Turn on
            success, _, error = self.run_command(f"sudo ifconfig {self.ethernet_interface} up", use_sudo_password=True)
            if not success:
                print(f"Error turning on ethernet: {error}")
                return False
            time.sleep(30)  # Give it time to connect and get DHCP
        
        return True
    
    def check_tailscale_status(self):
        """Check if Tailscale is running and connected"""
        # Try different possible Tailscale binary locations
        tailscale_paths = [
            "tailscale",  # If in PATH
            "/Applications/Tailscale.app/Contents/MacOS/Tailscale",  # macOS app bundle
            "/usr/bin/tailscale",  # Common Linux location
            "/usr/local/bin/tailscale"  # Homebrew location
        ]
        
        for tailscale_cmd in tailscale_paths:
            success, output, _ = self.run_command(f"{tailscale_cmd} status")
            if success:
                # Check if Tailscale is running and has a connection
                if "Tailscale is stopped" in output or "not running" in output:
                    return False, "stopped"
                elif output.strip():  # If there's output, it's likely running
                    # Check if we have active connections
                    lines = output.split('\n')
                    for line in lines:
                        if 'active' in line:
                            return True, "connected"
                    return True, "running"
        return False, "unknown"
    
    def restart_tailscale(self):
        """Restart Tailscale service"""
        print(f"[{datetime.now()}] Restarting Tailscale...")
        
        # Find the correct Tailscale binary path
        tailscale_cmd = None
        tailscale_paths = [
            "tailscale",
            "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
            "/usr/bin/tailscale",
            "/usr/local/bin/tailscale"
        ]
        
        for path in tailscale_paths:
            success, _, _ = self.run_command(f"{path} status")
            if success:
                tailscale_cmd = path
                break
        
        if not tailscale_cmd:
            print("Error: Could not find Tailscale binary")
            return False
        
        # Stop Tailscale
        self.run_command(f"sudo {tailscale_cmd} down", use_sudo_password=True)
        time.sleep(2)
        
        # Start Tailscale
        success, _, error = self.run_command(f"sudo {tailscale_cmd} up", use_sudo_password=True)
        if not success:
            print(f"Error starting Tailscale: {error}")
            return False
        
        time.sleep(5)  # Give it time to connect
        return True
    
    def fix_connectivity_issues(self):
        """Main logic to fix connectivity issues"""
        ethernet_active = self.check_ethernet_status()
        internet_connected = self.check_internet_connection()
        tailscale_running, tailscale_status = self.check_tailscale_status()
        
        print(f"[{datetime.now()}] Status Check:")
        print(f"  - Ethernet ({self.ethernet_interface}): {'Active' if ethernet_active else 'Inactive'}")
        print(f"  - Internet: {'Connected' if internet_connected else 'Disconnected'}")
        print(f"  - Tailscale: {tailscale_status}")
        
        issues_fixed = False
        
        # Fix ethernet if needed
        if not ethernet_active:
            # If ethernet is disabled, enable it
            print(f"[{datetime.now()}] Ethernet interface disabled, enabling it...")
            self.toggle_ethernet("on")
            issues_fixed = True
            time.sleep(5)
            
            # Re-check status after enabling
            ethernet_active = self.check_ethernet_status()
            internet_connected = self.check_internet_connection()
        elif ethernet_active and not internet_connected:
            # If ethernet is active but no internet, restart it
            print(f"[{datetime.now()}] Ethernet active but no internet, restarting ethernet...")
            self.toggle_ethernet("restart")
            issues_fixed = True
            time.sleep(5)
            
            # Re-check internet after restart
            internet_connected = self.check_internet_connection()
        
        # Fix internet connectivity by restarting ethernet if still no internet
        if not internet_connected:
            print(f"[{datetime.now()}] Still no internet, restarting ethernet interface...")
            self.toggle_ethernet("restart")
            issues_fixed = True
            time.sleep(5)
            
            # Re-check internet after ethernet restart
            internet_connected = self.check_internet_connection()
        
        # Fix Tailscale if needed (after fixing ethernet)
        if not internet_connected:
            print(f"[{datetime.now()}] Internet still down after ethernet fix, restarting Tailscale...")
            self.restart_tailscale()
            issues_fixed = True
            time.sleep(5)
            
            # Final check
            internet_connected = self.check_internet_connection()
            if internet_connected:
                print(f"[{datetime.now()}] Internet connectivity restored!")
            else:
                print(f"[{datetime.now()}] Still no internet connectivity, may need manual intervention")
        
        if not issues_fixed:
            print(f"[{datetime.now()}] All systems operational")
        
        return internet_connected
    
    def monitor_loop(self):
        """Continuous monitoring loop"""
        # Register with monitoring system
        monitor.register()

        print(f"Starting network monitoring (checking every {self.check_interval} seconds)...")
        print(f"Ethernet interface: {self.ethernet_interface}")
        print("Press Ctrl+C to stop\n")

        # On first run, always check and fix Tailscale if needed
        if self.tailscale_restart_on_boot:
            print(f"[{datetime.now()}] Initial startup check...")
            self.fix_connectivity_issues()
            self.tailscale_restart_on_boot = False

        try:
            while True:
                # Send heartbeat at start of each cycle
                monitor.send_heartbeat()

                time.sleep(self.check_interval)
                self.fix_connectivity_issues()
                print("-" * 50)
        except KeyboardInterrupt:
            print("\nMonitoring stopped.")
    
    def run_once(self):
        """Run the fix once and exit"""
        print("Running network fix once...")
        success = self.fix_connectivity_issues()
        return success
    
    def test_connectivity_logic(self):
        """Test different connectivity scenarios"""
        print("Testing connectivity fix scenarios...\n")
        
        # Test scenario 1: Ethernet disabled
        print("=== Scenario 1: Ethernet Disabled ===")
        original_check = self.check_ethernet_status
        self.check_ethernet_status = lambda: False  # Mock disabled ethernet
        self.fix_connectivity_issues()
        self.check_ethernet_status = original_check  # Restore
        
        print("\n" + "-"*50 + "\n")
        
        # Test scenario 2: Ethernet active, no internet
        print("=== Scenario 2: Ethernet Active, No Internet ===")
        original_internet = self.check_internet_connection
        self.check_internet_connection = lambda: False  # Mock no internet
        self.fix_connectivity_issues()
        self.check_internet_connection = original_internet  # Restore
        
        print("\n" + "-"*50 + "\n")
        
        # Test scenario 3: All systems operational
        print("=== Scenario 3: All Systems Operational ===")
        self.fix_connectivity_issues()
    
    def test_sudo_password(self):
        """Test sudo password authentication"""
        print(f"Testing sudo password authentication...")
        print(f"Using password: {self.ADMIN_PASSWORD}")
        
        # Test simple sudo command that doesn't affect system
        test_commands = [
            "sudo -S whoami",
            "sudo -S echo 'Test successful'",
            f"sudo -S ifconfig {self.ethernet_interface}",
            f"sudo -S ifconfig {self.ethernet_interface} down",
            f"sudo -S ifconfig {self.ethernet_interface} up"
        ]
        
        for cmd in test_commands:
            print(f"\nTesting command: {cmd}")
            success, output, error = self.run_command(cmd, use_sudo_password=True)
            print(f"Success: {success}")
            print(f"Output: {output}")
            if error:
                print(f"Error: {error}")
        
        # Also test the raw echo command format
        raw_cmd = f"echo '{self.ADMIN_PASSWORD}' | sudo -S whoami"
        print(f"\nTesting raw command: {raw_cmd}")
        success, output, error = self.run_command(raw_cmd)
        print(f"Raw command success: {success}")
        print(f"Raw command output: {output}")
        if error:
            print(f"Raw command error: {error}")

def main():
    parser = argparse.ArgumentParser(description='Network Connection Manager for macOS')
    parser.add_argument('-i', '--interface', default='en0', help='Ethernet interface (default: en0)')
    parser.add_argument('-t', '--interval', type=int, default=30, help='Check interval in seconds (default: 30)')
    parser.add_argument('-o', '--once', action='store_true', help='Run once and exit')
    parser.add_argument('-l', '--list', action='store_true', help='List available ethernet interfaces')
    parser.add_argument('--test', action='store_true', help='Test sudo password authentication')
    parser.add_argument('--test-logic', action='store_true', help='Test connectivity fix logic scenarios')
    
    args = parser.parse_args()
    
    # Check if running with sudo (required for network operations)
    # if not args.list and os.geteuid() != 0:
    #     print("This script requires sudo privileges to manage network interfaces.")
    #     print("Please run with: sudo python3 network_manager.py")
    #     sys.exit(1)
    
    manager = NetworkManager(ethernet_interface=args.interface, check_interval=args.interval)
    
    if args.list:
        print("Available ethernet interfaces:")
        interfaces = manager.get_ethernet_interfaces()
        for iface in interfaces:
            print(f"  - {iface}")
        sys.exit(0)
    
    if args.test:
        manager.test_sudo_password()
        sys.exit(0)
    
    if hasattr(args, 'test_logic') and args.test_logic:
        manager.test_connectivity_logic()
        sys.exit(0)
    
    if args.once:
        success = manager.run_once()
        sys.exit(0 if success else 1)
    else:
        manager.monitor_loop()

if __name__ == "__main__":
    main()