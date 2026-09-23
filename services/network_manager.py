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
import json
import argparse
from datetime import datetime
sys.path.insert(0, '/Users/signlab/drs/shared')
from signcollect_monitor import SignCollectMonitor  # Import the monitor client

# Initialize the monitor
monitor = SignCollectMonitor(
    client_id='drs-network-manager',
    client_name='DRS Network Manager',
    description='Ethernet and Tailscale connectivity monitor',
    heartbeat_interval=300
)

class NetworkManager:
    def __init__(self, ethernet_interface="en0", check_interval=30):
        self.ethernet_interface = ethernet_interface
        self.check_interval = check_interval
        self.tailscale_restart_on_boot = True
        self._tailscale_cmd = None  # Cached path to the Tailscale binary
        # Sudo runs non-interactively and relies on /etc/sudoers.d/signlab-network
        # (NOPASSWD for the exact ifconfig/Tailscale commands). If that rule is
        # missing, disable sudo-based fixes instead of failing every cycle.
        self.sudo_disabled = False
        
    def run_command(self, command, shell=True, use_sudo_password=False):
        """Run a shell command and return output"""
        try:
            if use_sudo_password and command.strip().startswith('sudo'):
                if self.sudo_disabled:
                    return False, "", "sudo disabled: no passwordless sudo rule available"
                # Never pipe a password: -n makes sudo fail immediately instead
                # of prompting, so a missing sudoers rule can't hang the loop
                # or rack up failed authentications that lock the account.
                command = command.replace('sudo ', 'sudo -n ', 1)

            result = subprocess.run(command, shell=shell, capture_output=True, text=True)
            if use_sudo_password and 'password is required' in (result.stderr or '').lower():
                self.sudo_disabled = True
                print(f"[{datetime.now()}] Passwordless sudo not configured - disabling all "
                      f"sudo-based fixes. Install /etc/sudoers.d/signlab-network with "
                      f"NOPASSWD rules for ifconfig and Tailscale to re-enable them.", flush=True)
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
    
    def find_tailscale_cmd(self):
        """Locate and cache the Tailscale binary path (empty string if none)."""
        if self._tailscale_cmd is not None:
            return self._tailscale_cmd
        for path in [
            "/Applications/Tailscale.app/Contents/MacOS/Tailscale",  # macOS app bundle
            "tailscale",                 # If in PATH
            "/opt/homebrew/bin/tailscale",  # Homebrew (Apple Silicon)
            "/usr/local/bin/tailscale",  # Homebrew (Intel)
            "/usr/bin/tailscale",        # Common Linux location
        ]:
            success, _, _ = self.run_command(f"{path} version")
            if success:
                self._tailscale_cmd = path
                return path
        self._tailscale_cmd = ""  # Cache the miss so we don't probe every cycle
        return ""

    def check_tailscale_connected(self):
        """Robustly determine whether Tailscale is actually on the tailnet.

        Uses `tailscale status --json` and requires BOTH the backend to be
        Running AND this node to be seen Online by the control plane. This
        catches the post-reboot case where the backend is up but hung while
        reconnecting (which the human-readable string parse can miss).
        """
        cmd = self.find_tailscale_cmd()
        if not cmd:
            return False
        success, output, _ = self.run_command(f"{cmd} status --json")
        if not success or not output:
            return False
        try:
            data = json.loads(output)
        except (ValueError, TypeError):
            return False
        backend = data.get("BackendState")
        online = data.get("Self", {}).get("Online", False)
        return backend == "Running" and bool(online)

    def tailscale_up(self):
        """Bring Tailscale up (a gentle nudge, no down first)."""
        cmd = self.find_tailscale_cmd()
        if not cmd:
            print("Error: Could not find Tailscale binary")
            return False
        success, _, error = self.run_command(f"sudo {cmd} up", use_sudo_password=True)
        if not success:
            print(f"Error bringing Tailscale up: {error}")
        return success

    def ensure_tailscale_connected(self, max_attempts=4):
        """Make sure Tailscale is connected to the tailnet, escalating until it is.

        Mirrors the manual recovery that works in the studio: first nudge
        Tailscale up, then fully restart it, then toggle ethernet (down/up)
        before bringing Tailscale up again. Bounded per cycle - the monitor
        loop retries on the next pass if it still hasn't connected.
        """
        if self.check_tailscale_connected():
            return True

        print(f"[{datetime.now()}] Tailscale not on tailnet - starting recovery...")
        for attempt in range(1, max_attempts + 1):
            if attempt == 1:
                print(f"[{datetime.now()}] Tailscale recovery attempt {attempt}: 'tailscale up'")
                self.tailscale_up()
            elif attempt == 2:
                print(f"[{datetime.now()}] Tailscale recovery attempt {attempt}: restart Tailscale")
                self.restart_tailscale()
            else:
                print(f"[{datetime.now()}] Tailscale recovery attempt {attempt}: toggle ethernet + Tailscale up")
                self.toggle_ethernet("restart")  # down/up en0 - this is what usually unsticks it
                self.tailscale_up()

            time.sleep(8)  # Give the backend time to settle and reach the control plane
            if self.check_tailscale_connected():
                print(f"[{datetime.now()}] Tailscale reconnected to tailnet (attempt {attempt}).")
                return True

        print(f"[{datetime.now()}] Tailscale still not on tailnet after {max_attempts} attempts - will retry next cycle.")
        return False

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

        # Only touch ethernet when there is actually no internet: the studio
        # deliberately runs on Wi-Fi with ethernet unplugged, and forcing en0
        # up every cycle just burns sudo attempts.
        if not internet_connected and not ethernet_active:
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
        
        # Ensure Tailscale is actually on the tailnet, independent of general
        # internet connectivity. Covers the common post-reboot case where the
        # machine has working internet but Tailscale hung while reconnecting.
        if internet_connected and not self.check_tailscale_connected():
            print(f"[{datetime.now()}] Internet OK but Tailscale not on tailnet - recovering...")
            self.ensure_tailscale_connected()
            issues_fixed = True

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
    
    def check_passwordless_sudo(self):
        """Test that the passwordless sudo rules this service relies on work."""
        print("Testing passwordless sudo (expects /etc/sudoers.d/signlab-network)...")

        test_commands = [
            f"sudo ifconfig {self.ethernet_interface} up",
            "sudo /Applications/Tailscale.app/Contents/MacOS/Tailscale up",
        ]

        for cmd in test_commands:
            print(f"\nTesting command: {cmd}")
            success, output, error = self.run_command(cmd, use_sudo_password=True)
            print(f"Success: {success}")
            if output:
                print(f"Output: {output}")
            if error:
                print(f"Error: {error}")
        if self.sudo_disabled:
            print("\nPasswordless sudo is NOT configured - install the sudoers rule:")
            print('  sudo sh -c \'echo "signlab ALL=(root) NOPASSWD: /sbin/ifconfig en0 up, '
                  '/sbin/ifconfig en0 down, /Applications/Tailscale.app/Contents/MacOS/Tailscale up, '
                  '/Applications/Tailscale.app/Contents/MacOS/Tailscale down" '
                  '> /etc/sudoers.d/signlab-network && visudo -c\'')

def main():
    parser = argparse.ArgumentParser(description='Network Connection Manager for macOS')
    parser.add_argument('-i', '--interface', default='en0', help='Ethernet interface (default: en0)')
    parser.add_argument('-t', '--interval', type=int, default=30, help='Check interval in seconds (default: 30)')
    parser.add_argument('-o', '--once', action='store_true', help='Run once and exit')
    parser.add_argument('-l', '--list', action='store_true', help='List available ethernet interfaces')
    parser.add_argument('--test', action='store_true', help="Check the passwordless sudo rules (/etc/sudoers.d/signlab-network)")
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
        manager.check_passwordless_sudo()
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