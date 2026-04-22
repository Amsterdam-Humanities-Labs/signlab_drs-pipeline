# usb_monitor_pyqt.py

import sys
import re
import subprocess
import time
import threading
import os  # Moved import to the top for proper usage
import json
import socket
from datetime import datetime
import requests
import websocket
from PyQt5.QtWidgets import (
    QApplication, QWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QLabel, QPushButton, QTextEdit, QMessageBox, QHBoxLayout, QDialog, QTextBrowser,
    QCheckBox
)
from PyQt5.QtGui import QColor, QBrush, QFont
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject

# ----------------------------
# Step 1: Define the Serial Number Mapping
# ----------------------------
SERIAL_MAPPING = {
    "D4DA001EAC65": "Camera M",
    "D4DA001EACEA": "Camera R",
    "D4DA001ECB4B": "Camera A",
    "D4DA001EAD5C": "Camera L",
    "D4DA001EC952": "Camera B"
    # Add more mappings as needed
}

# ----------------------------
# Step 2: Define the USB Output Source
# ----------------------------
# For demonstration purposes, we will fetch USB data using a system command.
# On macOS, use 'system_profiler SPUSBDataType'.
# On Linux, use 'lsusb'.
# Adjust the fetch_usb_output function accordingly.

def fetch_usb_output():
    """
    Fetches the current USB output from the system.
    Adjust the command based on your operating system.
    """
    try:
        if sys.platform == "darwin":
            # macOS
            cmd = ["system_profiler", "SPUSBDataType"]
        elif sys.platform.startswith("linux"):
            # Linux
            cmd = ["lsusb"]
        else:
            # Unsupported OS
            return ""
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return result.stdout
    except subprocess.CalledProcessError as e:
        return ""

# ----------------------------
# Step 3: Function to Extract Serial Numbers
# ----------------------------
def extract_serial_numbers(usb_output):
    """
    Extracts all Serial Number values from the USB output.
    Specifically tailored for macOS 'system_profiler SPUSBDataType' output.
    """
    # Regular expression to match lines like "Serial Number: D4DA001EAC65"
    pattern = r'Serial Number:\s*([A-F0-9]{12})'
    serials = re.findall(pattern, usb_output)
    return serials

# For Linux 'lsusb', the output format is different. Implement extraction accordingly.
def extract_serial_numbers_lsusb(usb_output):
    """
    Extracts serial numbers from lsusb output on Linux.
    This is a placeholder and may need adjustment based on actual lsusb output.
    """
    # Example lsusb output line:
    # Bus 002 Device 003: ID 1507:1554 GenesysLogic USB 3.0 Hub
    # Serial number extraction may require additional commands or flags.
    # Here, we'll return empty as a placeholder.
    return []

# ----------------------------
# Step 4: Worker Classes for Processes
# ----------------------------

class Worker(QObject):
    """
    Worker class to handle the restart process in a separate thread.
    This includes shutting down cameras, restarting RemoteCli, and starting cameras.
    """
    log_signal = pyqtSignal(str, QColor)
    finished_signal = pyqtSignal()

    def __init__(self, serials_to_manage, serial_mapping, remote_cli_path):
        super().__init__()
        self.serials_to_manage = serials_to_manage
        self.serial_mapping = serial_mapping
        self.remote_cli_path = remote_cli_path
        self._is_running = True

    def log(self, message, color=Qt.black):
        """
        Emit a log message with the specified color.
        """
        self.log_signal.emit(message, QColor(color))

    def run(self):
        """
        Executes the restart process as specified.
        """
        try:
            # Step 1: Display "doe alle cameras uit"
            self.log("Doe alle cameras uit...", QColor('blue'))

            # Step 2: Turn off all cameras
            # Implement the actual command to turn off cameras here.
            # This is a placeholder.
            # Example:
            # subprocess.run(["/path/to/turn_off_cameras.sh"], check=True)
            time.sleep(2)  # Simulate time taken to turn off cameras

            # Step 3: Check every second if cameras are disconnected
            self.log("Controleren of alle cameras zijn uitgeschakeld...", QColor('blue'))
            cameras_off = False
            for _ in range(10):  # Wait up to 10 seconds
                if not self.are_cameras_online():
                    cameras_off = True
                    break
                time.sleep(1)

            if cameras_off:
                self.log("Goed! Bezig met stuurprogramma stopzetten", QColor('green'))
            else:
                self.log("Niet alle cameras zijn uitgeschakeld. Probeer opnieuw.", QColor('red'))
                self.finished_signal.emit()
                return

            # Step 4: Kill RemoteCli
            self.log("RemoteCli wordt gestopt...", QColor('blue'))
            try:
                subprocess.run(["pkill", "-f", self.remote_cli_path], check=True)
                self.log("RemoteCli succesvol gestopt.", QColor('green'))
            except subprocess.CalledProcessError:
                # RemoteCli process not found; continue anyway
                self.log("RemoteCli proces niet gevonden. Doorgaan...", QColor('orange'))

            # Step 5: Display "Doe alle cameras aan"
            self.log("Doe alle cameras aan...", QColor('blue'))

            # Step 6: Turn on all cameras
            # Implement the actual command to turn on cameras here.
            # This is a placeholder.
            # Example:
            # subprocess.run(["/path/to/turn_on_cameras.sh"], check=True)
            time.sleep(2)  # Simulate time taken to turn on cameras

            # Step 7: Wait for all cameras to come online
            self.log("Wachten tot alle cameras online zijn...", QColor('blue'))
            cameras_online = False
            for _ in range(20):  # Wait up to 20 seconds
                if self.are_cameras_online():
                    cameras_online = True
                    break
                time.sleep(1)

            if cameras_online:
                self.log("RemoteCli opstarten....", QColor('blue'))
                # Step 8: Start RemoteCli and capture its output
                try:
                    self.process = subprocess.Popen(
                        [self.remote_cli_path],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True
                    )
                    # Read the output in a separate thread
                    threading.Thread(target=self.read_remote_cli_output, daemon=True).start()
                    self.log("RemoteCli succesvol opgestart.", QColor('green'))
                except Exception as e:
                    self.log(f"Fout bij het opstarten van RemoteCli: {e}", QColor('red'))
            else:
                self.log("Niet alle cameras zijn online. Probeer opnieuw.", QColor('red'))

        finally:
            self.finished_signal.emit()

    def read_remote_cli_output(self):
        """
        Reads the output from RemoteCli and emits log messages.
        """
        if hasattr(self, 'process') and self.process.stdout:
            for line in self.process.stdout:
                self.log(line.strip(), QColor('black'))
            self.process.stdout.close()

    def are_cameras_online(self):
        """
        Checks if all cameras are online by verifying their serial numbers are present.
        """
        usb_output = fetch_usb_output()
        if sys.platform.startswith("linux"):
            serials_present = extract_serial_numbers_lsusb(usb_output)
        else:
            serials_present = extract_serial_numbers(usb_output)

        for serial in self.serials_to_manage:
            if serial not in serials_present:
                return False
        return True

class StartupWorker(QObject):
    """
    Worker class to handle the startup check for RemoteCli in a separate thread.
    """
    log_signal = pyqtSignal(str, QColor)
    finished_signal = pyqtSignal()

    def __init__(self, remote_cli_path):
        super().__init__()
        self.remote_cli_path = remote_cli_path

    def log(self, message, color=Qt.black):
        """
        Emit a log message with the specified color.
        """
        self.log_signal.emit(message, QColor(color))

    def run(self):
        """
        Checks if RemoteCli is running. If not, starts it and captures output.
        """
        try:
            if self.is_remote_cli_running():
                self.log("RemoteCli is al actief.", QColor('green'))
            else:
                self.log("RemoteCli is niet actief. Opstarten...", QColor('blue'))
                self.process = subprocess.Popen(
                    [self.remote_cli_path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True
                )
                self.log("RemoteCli succesvol opgestart.", QColor('green'))
                # Read the output in a separate thread
                threading.Thread(target=self.read_remote_cli_output, daemon=True).start()
        except Exception as e:
            self.log(f"Fout bij het controleren/opstarten van RemoteCli: {e}", QColor('red'))
        finally:
            self.finished_signal.emit()

    def read_remote_cli_output(self):
        """
        Reads the output from RemoteCli and emits log messages.
        """
        if hasattr(self, 'process') and self.process.stdout:
            for line in self.process.stdout:
                self.log(line.strip(), QColor('black'))
            self.process.stdout.close()

    def is_remote_cli_running(self):
        """
        Checks if RemoteCli is currently running.
        """
        try:
            # Use pgrep to check if the process is running
            result = subprocess.run(
                ["pgrep", "-f", self.remote_cli_path],
                capture_output=True,
                text=True
            )
            return result.returncode == 0
        except Exception:
            return False

class RemoteCliRestartWorker(QObject):
    """
    Worker class to handle the independent restart of RemoteCli in a separate thread.
    """
    log_signal = pyqtSignal(str, QColor)
    finished_signal = pyqtSignal()

    def __init__(self, remote_cli_path):
        super().__init__()
        self.remote_cli_path = remote_cli_path

    def log(self, message, color=Qt.black):
        """
        Emit a log message with the specified color.
        """
        self.log_signal.emit(message, QColor(color))

    def run(self):
        """
        Restarts RemoteCli independently of camera shutdown/startup.
        """
        try:
            self.log("Force Restart RemoteCli gestart...", QColor('blue'))

            # Step 1: Kill RemoteCli if it's running
            self.log("RemoteCli wordt gestopt...", QColor('blue'))
            try:
                subprocess.run(["pkill", "-f", self.remote_cli_path], check=True)
                self.log("RemoteCli succesvol gestopt.", QColor('green'))
            except subprocess.CalledProcessError:
                # RemoteCli process not found; continue anyway
                self.log("RemoteCli proces niet gevonden. Doorgaan...", QColor('orange'))

            # Step 2: Start RemoteCli
            self.log("RemoteCli wordt gestart...", QColor('blue'))
            try:
                self.process = subprocess.Popen(
                    [self.remote_cli_path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True
                )
                self.log("RemoteCli succesvol opgestart.", QColor('green'))
                # Read the output in a separate thread
                threading.Thread(target=self.read_remote_cli_output, daemon=True).start()
            except Exception as e:
                self.log(f"Fout bij het opstarten van RemoteCli: {e}", QColor('red'))

        except Exception as e:
            self.log(f"Onverwachte fout: {e}", QColor('red'))
        finally:
            self.finished_signal.emit()

    def read_remote_cli_output(self):
        """
        Reads the output from RemoteCli and emits log messages.
        """
        if hasattr(self, 'process') and self.process.stdout:
            for line in self.process.stdout:
                self.log(line.strip(), QColor('black'))
            self.process.stdout.close()

class HelpWindow(QDialog):
    """
    Help Window displaying FAQs and step-by-step instructions.
    """
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Handleiding / FAQ")
        self.setGeometry(100, 100, 600, 600)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()

        # Use QTextBrowser for rich text display
        self.text_browser = QTextBrowser()
        self.text_browser.setOpenExternalLinks(True)  # Allow opening links if needed
        self.text_browser.setHtml(self.get_help_content())
        layout.addWidget(self.text_browser)

        self.setLayout(layout)

    def get_help_content(self):
        """
        Returns the HTML content for the help window.
        """
        return """
        <h1>Fileserver is offline....</h1>
        <p>Als Fileserver offline is, dan betekent dat RemoteCli proces niet meer draait of vast is gelopen. Druk dan op <b>Force Restart RemoteCli</b> of <b>Restart RemoteCli</b>. Mocht dat niet werken, doe eerst alle cameras uit, controleer bij Camera Status of ze allemaal Offline zijn. Druk dan weer op <b>Restart RemoteCli</b>.</p>

        <h1>Een van cameras zijn offline...</h1>
        <p>Stappenplan:</p>
        <ul>
            <li>Controleer of kabel goed is aangesloten</li>
            <li>Is batterij vol?</li>
            <li>Doe camera uit en dan aan</li>
            <li>Forceer Restart RemoteCli</li>

        </ul>

        <h1>Alle cameras zijn offline en RemoteCli restart helpt niet!</h1>
        <p>Dit kan gebeuren als er een kortsluiting is in interne systeem van deze computer of in een van cameras, want er kan ergens reststroom blijven hangen. Er zijn veel kabels met elkaar verbonden wat het lastig op te lossen probleen maakt.</p>
        <p>Als je al een aantal de computer opnieuw hebt opgestart en dit blijft voordoen, volg dan deze:</p>
        <p>Stappenplan:</p>
        <ul>
            <li>Zet alle cameras uit</li>
            <li>Zet ook deze computer uit</li>
            <li>Trekt alle stekkers los, zowel die van computer, Dell Docking, Anker docking en cameras.</li>
            <li>Wacht 1 minuut</li>
            <li>Zet alle stekkers er weer in</li>
            <li>Zet alles weer aan</li>
            <li>Mocht dat niet helpen, bel dan Gomer.</li>
        </ul>
        """

# ----------------------------
# Step 5: PyQt5 Application with Console and Restart Buttons
# ----------------------------

class USBMonitor(QWidget):
    def __init__(self, serial_mapping, remote_cli_path):
        super().__init__()
        self.serial_mapping = serial_mapping
        self.remote_cli_path = remote_cli_path
        self.websocket_client = None
        self.file_status_data = None
        self.download_counts = {}  # Track downloads per camera
        self.download_started = False  # Track if download session has started
        self.last_activity_time = datetime.now()  # Track last activity
        self.inactivity_timer = None  # Timer for 12-hour inactivity
        self.inactivity_warning_shown = False  # Track if warning was shown
        self.completed_cameras = set()  # Track which cameras have sent completedMultiple
        self.camera_connection_status = {}  # Track connection status per camera ID
        self.download_directory = None  # Store the download directory for file counting
        self.init_ui()
        self.init_timers()
        self.startup_check_remote_cli()
        self.init_remote_cli_status()
        self.init_websocket_connection()

    def init_ui(self):
        self.setWindowTitle('USB Device Monitor')
        
        # Set main window background to light color
        self.setStyleSheet("""
            QWidget {
                background-color: #f0f0f0;
                color: #000000;
            }
            QTableWidget {
                background-color: #ffffff;
                color: #000000;
                gridline-color: #cccccc;
                selection-background-color: #e6f3ff;
            }
            QTextEdit {
                background-color: #ffffff;
                color: #000000;
                border: 1px solid #cccccc;
            }
            QPushButton {
                background-color: #e8e8e8;
                color: #000000;
                border: 1px solid #999999;
                padding: 8px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #d0d0d0;
            }
            QPushButton:pressed {
                background-color: #c0c0c0;
            }
            QPushButton:disabled {
                background-color: #f5f5f5;
                color: #888888;
            }
            QLabel {
                background-color: transparent;
                color: #000000;
            }
        """)

        # Set up the main vertical layout
        main_layout = QVBoxLayout()

        # Add a horizontal layout for the Help button at the top
        help_layout = QHBoxLayout()
        help_button = QPushButton("HELP! Handleiding hier...")
        help_button.setFont(QFont("Arial", 12))
        help_button.setStyleSheet("""
            QPushButton {
                background-color: #ffeb3b;
                color: #000000;
                font-weight: bold;
                border: 2px solid #f57f17;
            }
            QPushButton:hover {
                background-color: #fff176;
            }
        """)
        help_button.clicked.connect(self.show_help_window)
        help_layout.addWidget(help_button)
        help_layout.addStretch()  # Push the button to the left
        main_layout.addLayout(help_layout)

        # Add a label
        label = QLabel("Sony Camera Status")
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet("font-size: 18px; font-weight: bold; color: #000000; padding: 10px;")
        main_layout.addWidget(label)

        # Create the table
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Serial Number", "Device Name", "Status", "Connection Status"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setFont(QFont("Arial", 12))
        # Set table header styling
        self.table.horizontalHeader().setStyleSheet("""
            QHeaderView::section {
                background-color: #e0e0e0;
                color: #000000;
                padding: 8px;
                border: 1px solid #cccccc;
                font-weight: bold;
            }
        """)
        main_layout.addWidget(self.table)

        # Add a console area
        console_label = QLabel("Console Output")
        console_label.setAlignment(Qt.AlignCenter)
        console_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #000000; padding: 10px;")
        main_layout.addWidget(console_label)

        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setFont(QFont("Courier New", 10))
        # Set console specific styling for better readability
        self.console.setStyleSheet("""
            QTextEdit {
                background-color: #ffffff;
                color: #000000;
                border: 2px solid #cccccc;
                padding: 5px;
            }
        """)
        main_layout.addWidget(self.console)

        # Add System Status display (RemoteCli + Node.js Server)
        system_status_label = QLabel("System Status")
        system_status_label.setAlignment(Qt.AlignCenter)
        system_status_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #000000; padding: 10px;")
        main_layout.addWidget(system_status_label)
        
        status_layout = QHBoxLayout()
        
        # RemoteCli Status
        remotecli_label = QLabel("RemoteCli Status:")
        remotecli_label.setFont(QFont("Arial", 14))
        remotecli_label.setStyleSheet("color: #000000; font-weight: bold;")
        status_layout.addWidget(remotecli_label)

        self.remote_cli_status = QLabel("Unknown")
        self.remote_cli_status.setFont(QFont("Arial", 14, QFont.Bold))
        self.remote_cli_status.setStyleSheet("color: #ff6600; font-weight: bold;")
        status_layout.addWidget(self.remote_cli_status)
        
        # Separator
        status_layout.addWidget(QLabel(" | "))
        
        # Node.js Server Status
        nodejs_label = QLabel("Node.js Server:")
        nodejs_label.setFont(QFont("Arial", 14))
        nodejs_label.setStyleSheet("color: #000000; font-weight: bold;")
        status_layout.addWidget(nodejs_label)
        
        self.nodejs_status = QLabel("Unknown")
        self.nodejs_status.setFont(QFont("Arial", 14, QFont.Bold))
        self.nodejs_status.setStyleSheet("color: #ff6600; font-weight: bold;")
        status_layout.addWidget(self.nodejs_status)
        
        # Separator
        status_layout.addWidget(QLabel(" | "))
        
        # Inactivity Timer Status
        inactivity_label = QLabel("Inactivity:")
        inactivity_label.setFont(QFont("Arial", 14))
        inactivity_label.setStyleSheet("color: #000000; font-weight: bold;")
        status_layout.addWidget(inactivity_label)
        
        self.inactivity_status = QLabel("Active")
        self.inactivity_status.setFont(QFont("Arial", 14, QFont.Bold))
        self.inactivity_status.setStyleSheet("color: #008000; font-weight: bold;")
        status_layout.addWidget(self.inactivity_status)
        
        status_layout.addStretch()
        main_layout.addLayout(status_layout)

        # Add File Status Dashboard
        file_status_label = QLabel("File Status Dashboard")
        file_status_label.setAlignment(Qt.AlignCenter)
        file_status_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #000000; padding: 10px;")
        main_layout.addWidget(file_status_label)
        
        # Create file status table
        self.file_status_table = QTableWidget()
        self.file_status_table.setColumnCount(8)
        self.file_status_table.setHorizontalHeaderLabels([
            "Date", "Camera A", "Camera B", "Camera L", "Camera M", "Camera R", "Total", "Status"
        ])
        self.file_status_table.horizontalHeader().setStretchLastSection(True)
        self.file_status_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.file_status_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.file_status_table.setFont(QFont("Arial", 11))
        self.file_status_table.setMaximumHeight(200)  # Limit height
        self.file_status_table.horizontalHeader().setStyleSheet("""
            QHeaderView::section {
                background-color: #e0e0e0;
                color: #000000;
                padding: 6px;
                border: 1px solid #cccccc;
                font-weight: bold;
            }
        """)
        main_layout.addWidget(self.file_status_table)
        
        # Add download status display
        download_status_label = QLabel("Download Status")
        download_status_label.setAlignment(Qt.AlignCenter)
        download_status_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #000000; padding: 10px;")
        main_layout.addWidget(download_status_label)
        
        self.download_status_label = QLabel("No downloads in progress")
        self.download_status_label.setFont(QFont("Arial", 12))
        self.download_status_label.setStyleSheet("color: #000000; padding: 5px;")
        self.download_status_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(self.download_status_label)
        
        # Add format control panel
        format_control_layout = QHBoxLayout()
        
        self.refresh_status_button = QPushButton("Refresh Status")
        self.refresh_status_button.setFont(QFont("Arial", 12))
        self.refresh_status_button.setStyleSheet("""
            QPushButton {
                background-color: #4caf50;
                color: white;
                font-weight: bold;
                padding: 10px 20px;
                border: 2px solid #388e3c;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #66bb6a;
            }
        """)
        self.refresh_status_button.clicked.connect(self.update_file_status)
        format_control_layout.addWidget(self.refresh_status_button)
        
        
        format_control_layout.addStretch()
        main_layout.addLayout(format_control_layout)

        # Add buttons layout
        buttons_layout = QHBoxLayout()

        # Add a restart button for cameras and RemoteCli
        self.restart_button = QPushButton("Restart RemoteCli (Als een van Cameras vastloopt)")
        self.restart_button.setFont(QFont("Arial", 14))
        self.restart_button.setStyleSheet("""
            QPushButton {
                background-color: #2196f3;
                color: #ffffff;
                font-weight: bold;
                padding: 12px;
                border: 2px solid #1976d2;
            }
            QPushButton:hover {
                background-color: #42a5f5;
            }
            QPushButton:disabled {
                background-color: #bbbbbb;
                color: #666666;
                border: 2px solid #999999;
            }
        """)
        self.restart_button.clicked.connect(self.restart_remote_cli_with_cameras)
        buttons_layout.addWidget(self.restart_button)

        # Add a separate button to force restart RemoteCli independently
        self.force_restart_button = QPushButton("Force Restart RemoteCli")
        self.force_restart_button.setFont(QFont("Arial", 14))
        self.force_restart_button.setStyleSheet("""
            QPushButton {
                background-color: #ff5722;
                color: #ffffff;
                font-weight: bold;
                padding: 12px;
                border: 2px solid #d84315;
            }
            QPushButton:hover {
                background-color: #ff7043;
            }
            QPushButton:disabled {
                background-color: #bbbbbb;
                color: #666666;
                border: 2px solid #999999;
            }
        """)
        self.force_restart_button.clicked.connect(self.force_restart_remote_cli)
        buttons_layout.addWidget(self.force_restart_button)

        main_layout.addLayout(buttons_layout)

        self.setLayout(main_layout)
        self.resize(1200, 900)  # Increased size for new components
        self.show()

        # Initialize log colors with better contrast
        self.color_mapping = {
            'green': QColor(0, 128, 0),      # Darker green for better readability
            'red': QColor(200, 0, 0),        # Darker red for better readability
            'blue': QColor(0, 0, 200),       # Darker blue for better readability
            'black': QColor(0, 0, 0),
            'orange': QColor(255, 140, 0)    # Better orange contrast
        }

    def init_timers(self):
        # Timer for periodic USB checks every 10 seconds
        self.usb_timer = QTimer()
        self.usb_timer.timeout.connect(self.update_table)
        self.usb_timer.start(10000)  # 10,000 milliseconds = 10 seconds

        # Initial table population
        self.update_table()
        
        # Inactivity timer - check every minute
        self.inactivity_check_timer = QTimer()
        self.inactivity_check_timer.timeout.connect(self.check_inactivity)
        self.inactivity_check_timer.start(60000)  # 60,000 milliseconds = 1 minute
        
        # Camera completion check timer - check every 5 seconds
        self.camera_completion_timer = QTimer()
        self.camera_completion_timer.timeout.connect(self.check_camera_completion_status)
        self.camera_completion_timer.start(5000)  # 5,000 milliseconds = 5 seconds
        self.last_completion_check = False  # Track if we already triggered the scan

    def init_remote_cli_status(self):
        # Timer for checking system status (RemoteCli + Node.js) every 5 seconds
        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self.update_system_status)
        self.status_timer.start(5000)  # 5,000 milliseconds = 5 seconds
        
        # Timer for file status updates every 30 seconds
        self.file_status_timer = QTimer()
        self.file_status_timer.timeout.connect(self.update_file_status)
        self.file_status_timer.start(30000)  # 30,000 milliseconds = 30 seconds

        # Initial status checks
        self.update_system_status()
        self.update_file_status()

    def update_table(self):
        usb_output = fetch_usb_output()
        if sys.platform.startswith("linux"):
            serials_present = extract_serial_numbers_lsusb(usb_output)
        else:
            serials_present = extract_serial_numbers(usb_output)

        # Check if there's been a change in USB status (activity indicator)
        previous_status = getattr(self, '_previous_usb_status', set())
        current_status = set(serials_present)
        if previous_status != current_status:
            self.record_activity("USB status changed")
        self._previous_usb_status = current_status

        self.table.setRowCount(len(self.serial_mapping))
        for row, (serial, name) in enumerate(self.serial_mapping.items()):
            # Serial Number
            serial_item = QTableWidgetItem(serial)
            serial_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 0, serial_item)

            # Device Name
            name_item = QTableWidgetItem(name)
            name_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 1, name_item)

            # Status with better colors
            if serial in serials_present:
                status_text = "Online"
                color = QColor(0, 128, 0)  # Dark green
            else:
                status_text = "Offline"
                color = QColor(200, 0, 0)  # Dark red

            status_item = QTableWidgetItem(status_text)
            status_item.setTextAlignment(Qt.AlignCenter)
            status_item.setForeground(QBrush(color))
            # Set bold font for status
            font = status_item.font()
            font.setBold(True)
            status_item.setFont(font)
            self.table.setItem(row, 2, status_item)

            # Connection Status (4th column)
            connection_status = self.camera_connection_status.get(serial, "Unknown")
            
            # Determine connection status text and color
            if connection_status == "connected":
                conn_text = "Connected"
                conn_color = QColor(0, 128, 0)  # Green
            elif connection_status == "device_prop_failed":
                conn_text = "Failed"
                conn_color = QColor(200, 0, 0)  # Red
            elif connection_status == "recording":
                conn_text = "Recording"
                conn_color = QColor(0, 0, 0)  # Neutral (black)
            elif connection_status == "completedMultiple":
                conn_text = "Downloaden klaar"
                conn_color = QColor(0, 0, 0)  # Neutral (black)
            else:
                conn_text = "Unknown"
                conn_color = QColor(0, 0, 0)  # Neutral (black)
            
            conn_item = QTableWidgetItem(conn_text)
            conn_item.setTextAlignment(Qt.AlignCenter)
            conn_item.setForeground(QBrush(conn_color))
            # Set bold font for connection status
            conn_font = conn_item.font()
            conn_font.setBold(True)
            conn_item.setFont(conn_font)
            self.table.setItem(row, 3, conn_item)

        # Optional: Resize columns to fit contents
        self.table.resizeColumnsToContents()

    def log_message(self, message, color='black'):
        """
        Logs a message to the console with the specified color.
        """
        color_code = {
            'green': 'green',
            'red': 'red',
            'blue': 'blue',
            'black': 'black',
            'orange': 'orange'
        }.get(color, 'black')

        # Apply HTML formatting for color
        # QTextEdit supports rich text, so we can use HTML to color the text
        colored_message = f'<span style="color:{color_code};">{message}</span>'
        self.console.append(colored_message)

    def restart_remote_cli_with_cameras(self):
        """
        Initiates the restart process (cameras shutdown/startup and RemoteCli restart) in a separate thread.
        """
        # Record activity
        self.record_activity("RemoteCli restart initiated")
        
        # Disable the button to prevent multiple clicks
        self.restart_button.setEnabled(False)

        # Create a worker and thread
        self.worker = Worker(
            serials_to_manage=list(self.serial_mapping.keys()),
            serial_mapping=self.serial_mapping,
            remote_cli_path=self.remote_cli_path
        )
        self.worker_thread = threading.Thread(target=self.worker.run, daemon=True)

        # Connect signals
        self.worker.log_signal.connect(self.handle_log)
        self.worker.finished_signal.connect(self.handle_finished)

        # Start the thread
        self.worker_thread.start()

    def handle_log(self, message, color):
        """
        Handles log messages from the worker.
        """
        if isinstance(color, QColor):
            color_name = color.name()
            if color_name == "#008000":
                color_str = 'green'
            elif color_name == "#c80000":
                color_str = 'red'
            elif color_name == "#0000c8":
                color_str = 'blue'
            elif color_name == "#ff8c00":  # Orange
                color_str = 'orange'
            else:
                color_str = 'black'
        else:
            color_str = 'black'
        self.log_message(message, color_str)

    def handle_finished(self):
        """
        Re-enables the restart button when the worker is finished.
        """
        self.restart_button.setEnabled(True)

    def startup_check_remote_cli(self):
        """
        Checks if RemoteCli is running on startup. If not, starts it.
        """
        self.startup_worker = StartupWorker(self.remote_cli_path)
        self.startup_thread = threading.Thread(target=self.startup_worker.run, daemon=True)

        # Connect signals
        self.startup_worker.log_signal.connect(self.handle_log)
        self.startup_worker.finished_signal.connect(self.startup_finished)

        # Start the thread
        self.startup_thread.start()

    def startup_finished(self):
        """
        Placeholder for any actions needed after startup check.
        """
        pass

    def force_restart_remote_cli(self):
        """
        Initiates the independent restart of RemoteCli in a separate thread.
        """
        # Record activity
        self.record_activity("Force restart RemoteCli initiated")
        
        # Disable the button to prevent multiple clicks
        self.force_restart_button.setEnabled(False)

        # Create a worker and thread
        self.restart_worker = RemoteCliRestartWorker(self.remote_cli_path)
        self.restart_thread = threading.Thread(target=self.restart_worker.run, daemon=True)

        # Connect signals
        self.restart_worker.log_signal.connect(self.handle_log)
        self.restart_worker.finished_signal.connect(self.handle_restart_finished)

        # Start the thread
        self.restart_thread.start()

    def handle_restart_finished(self):
        """
        Re-enables the force restart button when the worker is finished.
        """
        self.force_restart_button.setEnabled(True)

    def update_remote_cli_status(self):
        """
        Checks if RemoteCli is running and updates the status label.
        """
        if self.is_remote_cli_running():
            self.remote_cli_status.setText("Running")
            self.remote_cli_status.setStyleSheet("color: #008000; font-weight: bold;")  # Dark green
        else:
            self.remote_cli_status.setText("Not Running")
            self.remote_cli_status.setStyleSheet("color: #c80000; font-weight: bold;")  # Dark red

    def is_remote_cli_running(self):
        """
        Checks if RemoteCli is currently running.
        """
        try:
            # Use pgrep to check if the process is running
            result = subprocess.run(
                ["pgrep", "-f", self.remote_cli_path],
                capture_output=True,
                text=True
            )
            return result.returncode == 0
        except Exception:
            return False

    def check_nodejs_server_status(self):
        """
        Check if Node.js server is running on port 8080
        """
        try:
            # Method 1: Process detection
            result = subprocess.run(
                ["pgrep", "-f", "startServer_beta.js"],
                capture_output=True, text=True
            )
            process_running = result.returncode == 0
            
            # Method 2: Port connectivity test
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            port_accessible = sock.connect_ex(('localhost', 8080)) == 0
            sock.close()
            
            return process_running and port_accessible
        except Exception:
            return False

    def update_system_status(self):
        """
        Updates both RemoteCli and Node.js server status
        """
        # Update RemoteCli status
        if self.is_remote_cli_running():
            self.remote_cli_status.setText("Running")
            self.remote_cli_status.setStyleSheet("color: #008000; font-weight: bold;")  # Dark green
        else:
            self.remote_cli_status.setText("Not Running")
            self.remote_cli_status.setStyleSheet("color: #c80000; font-weight: bold;")  # Dark red
        
        # Update Node.js server status
        if self.check_nodejs_server_status():
            self.nodejs_status.setText("Running")
            self.nodejs_status.setStyleSheet("color: #008000; font-weight: bold;")  # Dark green
        else:
            self.nodejs_status.setText("Not Running")
            self.nodejs_status.setStyleSheet("color: #c80000; font-weight: bold;")  # Dark red

    def fetch_file_status(self):
        """
        Fetch file status from API
        """
        try:
            response = requests.get(
                'https://signcollect.nl/listfiles.json',
                timeout=10,
                headers={'Accept': 'application/json'}
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.log_message(f"Error fetching file status: {e}", 'red')
            return None

    def update_file_status(self):
        """
        Update the file status table and format eligibility
        """
        data = self.fetch_file_status()
        if not data:
            # Could not fetch file status data
            return
        
        self.file_status_data = data
        folders = data.get('folders', [])
        
        # Get last 5 dates
        recent_folders = sorted(folders, key=lambda x: x['date'], reverse=True)[:5]
        
        # Update table
        self.file_status_table.setRowCount(len(recent_folders))
        
        for row, folder in enumerate(recent_folders):
            # Date
            date_item = QTableWidgetItem(folder['date'])
            date_item.setTextAlignment(Qt.AlignCenter)
            self.file_status_table.setItem(row, 0, date_item)
            
            # Camera file counts
            files = folder.get('files', {})
            for col, camera in enumerate(['A', 'B', 'L', 'M', 'R'], 1):
                # Handle both dict and list formats
                if isinstance(files, dict):
                    count = files.get(camera, 0)
                else:
                    # If files is a list or other type, default to 0
                    count = 0
                count_item = QTableWidgetItem(str(count))
                count_item.setTextAlignment(Qt.AlignCenter)
                self.file_status_table.setItem(row, col, count_item)
            
            # Total
            total_item = QTableWidgetItem(str(folder.get('total', 0)))
            total_item.setTextAlignment(Qt.AlignCenter)
            self.file_status_table.setItem(row, 6, total_item)
            
            # Status
            is_ok = folder.get('is_ok', False)
            status_text = "OK" if is_ok else "Issues"
            status_item = QTableWidgetItem(status_text)
            status_item.setTextAlignment(Qt.AlignCenter)
            
            if is_ok:
                status_item.setForeground(QBrush(QColor(0, 128, 0)))  # Green
            else:
                status_item.setForeground(QBrush(QColor(200, 0, 0)))  # Red
            
            font = status_item.font()
            font.setBold(True)
            status_item.setFont(font)
            self.file_status_table.setItem(row, 7, status_item)
        
        # Format eligibility check (for auto-format logic only)
        can_format = len(recent_folders) >= 5 and all(f.get('is_ok', False) for f in recent_folders[:5])
        
        # Resize columns to fit contents
        self.file_status_table.resizeColumnsToContents()
        
        self.log_message(f"File status updated: {len(recent_folders)} recent dates loaded", 'blue')

    def init_websocket_connection(self):
        """
        Initialize WebSocket connection to Node.js server
        """
        try:
            self.websocket_client = websocket.WebSocketApp(
                "ws://localhost:8081",
                on_message=self.on_websocket_message,
                on_error=self.on_websocket_error,
                on_close=self.on_websocket_close,
                on_open=self.on_websocket_open
            )
            # Run in separate thread
            self.ws_thread = threading.Thread(target=self.websocket_client.run_forever, daemon=True)
            self.ws_thread.start()
        except Exception as e:
            self.log_message(f"WebSocket connection failed: {e}", 'red')

    def on_websocket_open(self, ws):
        """
        Handle WebSocket connection opened
        """
        self.log_message("WebSocket connected to Node.js server", 'green')

    def on_websocket_message(self, ws, message):
        """
        Handle WebSocket messages from Node.js server
        """
        # Record activity on any WebSocket message
        self.record_activity("WebSocket message received")
        
        try:
            data = json.loads(message)
            handle = data.get('handle')
            camera_id = data.get('cameraId')
            
            # Track camera connection status based on handle
            if camera_id and handle:
                self.camera_connection_status[camera_id] = handle
                # Log the received message with timestamp
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]
                self.log_message(f"[{current_time}] Received JSON message:", 'blue')
                self.log_message(json.dumps(data, indent=2), 'black')
            
            if handle == 'formatCompleted':
                camera_id = data.get('cameraId', 'Unknown')
                format_msg = data.get('formatMessage', 'Completed')
                self.log_message(f"Format completed for {camera_id}: {format_msg}", 'green')
                
                # Format completed successfully
                
                # Refresh file status after formatting
                self.update_file_status()
                
            elif handle == 'contentlist':
                # Download session is starting
                self.download_started = True
                self.download_counts = {'A': 0, 'B': 0, 'L': 0, 'M': 0, 'R': 0}
                self.completed_cameras = set()  # Reset completed cameras tracking
                self.download_directory = None  # Reset download directory
                self.log_message("Download session started - tracking cameras", 'blue')
                self.update_download_status()
                
            elif handle == 'fileDownloaded':
                camera_id = data.get('cameraId', 'Unknown')
                file_data = data.get('file', '')
                
                # Extract and store directory path from first fileDownloaded message
                if file_data and not self.download_directory:
                    # Extract directory: /Users/admin/signCollect/studioFiles/2025-06-27/raw/A20250627_2420.MP4
                    # becomes: /Users/admin/signCollect/studioFiles/2025-06-27/raw
                    self.download_directory = '/'.join(file_data.split('/')[:-1])
                    self.log_message(f"Download directory set to: {self.download_directory}", 'blue')
                
                # Extract camera letter from cameraId or file name
                camera_letter = None
                if camera_id and len(camera_id) > 0:
                    # Try to get from camera mapping
                    for serial, name in self.serial_mapping.items():
                        if serial == camera_id:
                            camera_letter = name.split()[-1]  # Get last character from "Camera X"
                            break
                
                # If not found, try to extract from filename
                if not camera_letter and file_data:
                    filename = file_data if isinstance(file_data, str) else ''
                    # Extract filename from full path: /Users/admin/signCollect/studioFiles/2025-06-27/raw/L20250627_0963.MP4
                    if filename:
                        basename = filename.split('/')[-1]  # Get just the filename
                        if basename and len(basename) > 0 and basename[0] in ['A', 'B', 'L', 'M', 'R']:
                            camera_letter = basename[0]
                
                if camera_letter and camera_letter in self.download_counts:
                    self.download_counts[camera_letter] += 1
                    self.log_message(f"File downloaded for Camera {camera_letter}: {file_data}", 'blue')
                    self.update_download_status()
                else:
                    self.log_message(f"File downloaded for {camera_id}: {file_data}", 'blue')
                    
            elif handle == 'completedMultiple':
                # Extract camera ID from the message
                camera_id = data.get('cameraId', '')
                
                # Try to map camera ID to camera letter
                camera_letter = None
                if camera_id:
                    # Check if it's already a letter
                    if camera_id in ['A', 'B', 'L', 'M', 'R']:
                        camera_letter = camera_id
                    else:
                        # Try to map from serial number
                        for serial, name in self.serial_mapping.items():
                            if serial == camera_id:
                                camera_letter = name.split()[-1]  # Get last character from "Camera X"
                                break
                
                # Debug: log the mapping attempt
                self.log_message(f"Mapping camera_id '{camera_id}' to camera_letter '{camera_letter}'", 'blue')
                
                if camera_letter:
                    self.completed_cameras.add(camera_letter)
                    self.log_message(f"Camera {camera_letter} completed download (completedMultiple received)", 'blue')
                    self.log_message(f"Completed cameras: {sorted(self.completed_cameras)} ({len(self.completed_cameras)}/5)", 'blue')
                else:
                    self.log_message(f"completedMultiple received from unknown camera: {camera_id}", 'orange')
                
                # Check if all cameras have equal downloads
                self.check_download_completion()
                
        except json.JSONDecodeError:
            self.log_message(f"Invalid WebSocket message: {message}", 'orange')

    def on_websocket_error(self, ws, error):
        """
        Handle WebSocket errors
        """
        self.log_message(f"WebSocket error: {error}", 'red')

    def on_websocket_close(self, ws, close_status_code, close_msg):
        """
        Handle WebSocket connection closed
        """
        self.log_message("WebSocket connection closed", 'orange')


    def _execute_format_request(self):
        """
        Execute the format request in a separate thread
        """
        try:
            response = requests.get('http://localhost:8080/format_media_all', timeout=60)
            if response.status_code == 200:
                self.log_message("Format request sent successfully - waiting for completion...", 'green')
            else:
                self.log_message(f"Format request failed: HTTP {response.status_code}", 'red')
        except Exception as e:
            self.log_message(f"Format request error: {e}", 'red')

    def update_remote_cli_status(self):
        """
        Legacy method - now handled by update_system_status
        """
        self.update_system_status()

    def update_download_status(self):
        """
        Update the download status label with current counts
        """
        if not self.download_started:
            self.download_status_label.setText("No downloads in progress")
            return
            
        status_text = "Downloads per camera: "
        status_parts = []
        for camera, count in sorted(self.download_counts.items()):
            status_parts.append(f"Camera {camera}: {count}")
        
        status_text += ", ".join(status_parts)
        self.download_status_label.setText(status_text)
        
    def check_download_completion(self):
        """
        Check if all cameras have equal download counts and show appropriate message
        """
        if not self.download_started or not self.download_counts:
            return
            
        # Get all counts
        counts = list(self.download_counts.values())
        
        # Check if all counts are equal and greater than 0
        if counts and all(count == counts[0] for count in counts) and counts[0] > 0:
            self.log_message(f"All cameras have downloaded {counts[0]} files - OK to format!", 'green')
            self.download_status_label.setText(f"Download complete! All cameras: {counts[0]} files - OK to format")
            self.download_status_label.setStyleSheet("color: #008000; font-weight: bold; padding: 5px;")
            
            # Reset download tracking
            self.download_started = False
            self.download_counts = {}
            self.completed_cameras = set()
        else:
            # Show which cameras have different counts
            self.log_message("Download counts are not equal across cameras:", 'orange')
            for camera, count in sorted(self.download_counts.items()):
                self.log_message(f"  Camera {camera}: {count} files", 'orange')
            self.download_status_label.setStyleSheet("color: #ff8c00; font-weight: bold; padding: 5px;")
            
    def show_help_window(self):
        """
        Opens the Help Window displaying FAQs and instructions.
        """
        self.help_window = HelpWindow()
        self.help_window.exec_()
    
    def toggle_auto_format(self, state):
        """
        Toggle auto-format functionality
        """
        self.auto_format_enabled = state == Qt.Checked
        if self.auto_format_enabled:
            self.log_message("Auto-format enabled - will format after equal file downloads", 'blue')
        else:
            self.log_message("Auto-format disabled", 'blue')
            # Cancel any pending auto-format
            if self.auto_format_timer:
                self.auto_format_timer.stop()
                self.auto_format_timer = None
    
    def check_auto_format_eligibility(self):
        """
        Check if auto-format should be triggered after all cameras sent completedMultiple
        """
        # First verify we have received completedMultiple from all 5 cameras
        if len(self.completed_cameras) < 5:
            self.log_message(f"Auto-format check skipped: Only {len(self.completed_cameras)}/5 cameras completed", 'orange')
            return
        
        if not self.download_counts:
            self.log_message("Auto-format skipped: No download count data", 'orange')
            return
        
        # Get all counts
        counts = list(self.download_counts.values())
        
        # Check if all counts are equal and meet minimum threshold
        min_file_threshold = 50  # Minimum files per camera for auto-format
        
        if (counts and 
            all(count == counts[0] for count in counts) and 
            counts[0] >= min_file_threshold):
            
            # All cameras have equal counts and meet threshold
            self.log_message(f"✅ Auto-format eligible: All 5 cameras have {counts[0]} files each", 'green')
            self.show_auto_format_countdown(counts[0])
        else:
            # Log why auto-format is not eligible
            if not all(count == counts[0] for count in counts):
                self.log_message("Auto-format skipped: Unequal file counts across cameras", 'orange')
                for camera, count in sorted(self.download_counts.items()):
                    self.log_message(f"  Camera {camera}: {count} files", 'orange')
            elif counts[0] < min_file_threshold:
                self.log_message(f"Auto-format skipped: Only {counts[0]} files per camera (need {min_file_threshold}+)", 'orange')
    
    def show_auto_format_countdown(self, file_count):
        """
        Show countdown dialog for auto-format with cancel option
        """
        countdown_seconds = 10
        
        # Create custom dialog
        self.auto_format_dialog = QMessageBox(self)
        self.auto_format_dialog.setWindowTitle("Auto-Format Starting")
        self.auto_format_dialog.setIcon(QMessageBox.Warning)
        
        # Update message with countdown
        def update_countdown():
            nonlocal countdown_seconds
            countdown_seconds -= 1
            
            if countdown_seconds > 0:
                self.auto_format_dialog.setText(
                    f"✅ All cameras have downloaded {file_count} files successfully!\n\n"
                    f"⏰ Auto-formatting will start in {countdown_seconds} seconds...\n\n"
                    "Click Cancel to stop auto-format."
                )
            else:
                # Time's up - execute format
                self.auto_format_timer.stop()
                self.auto_format_dialog.accept()
                self.execute_auto_format()
        
        # Initial message
        self.auto_format_dialog.setText(
            f"✅ All cameras have downloaded {file_count} files successfully!\n\n"
            f"⏰ Auto-formatting will start in {countdown_seconds} seconds...\n\n"
            "Click Cancel to stop auto-format."
        )
        
        # Add cancel button
        cancel_btn = self.auto_format_dialog.addButton("Cancel Auto-Format", QMessageBox.RejectRole)
        
        # Start countdown timer
        self.auto_format_timer = QTimer()
        self.auto_format_timer.timeout.connect(update_countdown)
        self.auto_format_timer.start(1000)  # Update every second
        
        # Show dialog
        result = self.auto_format_dialog.exec_()
        
        # Stop timer if dialog was closed
        if self.auto_format_timer:
            self.auto_format_timer.stop()
            self.auto_format_timer = None
        
        if result == QMessageBox.Rejected:
            self.log_message("Auto-format cancelled by user", 'orange')
    
    def execute_auto_format(self):
        """
        Execute the auto-format operation
        """
        self.log_message("✅ Starting automatic format operation...", 'green')
        
        # Reset download tracking
        self.download_started = False
        self.download_counts = {}
        self.completed_cameras = set()
        self.download_status_label.setText("Auto-format in progress...")
        self.download_status_label.setStyleSheet("color: #0000c8; font-weight: bold; padding: 5px;")
        
        # Run format request in separate thread
        format_thread = threading.Thread(target=self._execute_format_request, daemon=True)
        format_thread.start()
    
    def record_activity(self, activity_type=""):
        """
        Record activity timestamp and reset inactivity state
        """
        self.last_activity_time = datetime.now()
        self.inactivity_warning_shown = False
        if activity_type:
            # Optionally log activity type for debugging
            pass
    
    def check_inactivity(self):
        """
        Check for inactivity and take action if necessary
        """
        current_time = datetime.now()
        time_since_activity = current_time - self.last_activity_time
        hours_inactive = time_since_activity.total_seconds() / 3600
        
        # Update inactivity status display
        if hours_inactive < 0.5:
            self.inactivity_status.setText("Active")
            self.inactivity_status.setStyleSheet("color: #008000; font-weight: bold;")
        elif hours_inactive < 11.5:
            self.inactivity_status.setText(f"{hours_inactive:.1f}h inactive")
            self.inactivity_status.setStyleSheet("color: #ff8c00; font-weight: bold;")
        else:
            self.inactivity_status.setText(f"{hours_inactive:.1f}h inactive!")
            self.inactivity_status.setStyleSheet("color: #c80000; font-weight: bold;")
        
        # Show warning at 11.5 hours
        if hours_inactive >= 11.5 and not self.inactivity_warning_shown:
            self.inactivity_warning_shown = True
            self.show_inactivity_warning()
        
        # Kill RemoteCli at 12 hours
        if hours_inactive >= 12.0:
            self.kill_remote_cli_due_to_inactivity()
    
    def show_inactivity_warning(self):
        """
        Show warning that RemoteCli will be killed soon
        """
        msg = QMessageBox(self)
        msg.setWindowTitle("Inactivity Warning")
        msg.setText(
            "⚠️ RemoteCli will be automatically stopped in 30 minutes due to inactivity!\n\n"
            "Click any button or perform any action to reset the timer."
        )
        msg.setIcon(QMessageBox.Warning)
        msg.addButton("Reset Timer", QMessageBox.AcceptRole)
        msg.addButton("Let it Stop", QMessageBox.RejectRole)
        
        result = msg.exec_()
        if result == 0:  # Reset Timer button
            self.record_activity("User reset inactivity timer")
            self.log_message("Inactivity timer reset by user", 'blue')
    
    def kill_remote_cli_due_to_inactivity(self):
        """
        Kill RemoteCli after 12 hours of inactivity
        """
        self.log_message("⏰ Killing RemoteCli due to 12 hours of inactivity", 'orange')
        
        try:
            subprocess.run(["pkill", "-f", self.remote_cli_path], check=True)
            self.log_message("RemoteCli stopped due to inactivity", 'orange')
        except subprocess.CalledProcessError:
            self.log_message("RemoteCli was not running", 'orange')
        
        # Reset activity time to prevent repeated kills
        self.last_activity_time = datetime.now()
        
        # Update status
        self.update_system_status()
    
    def mousePressEvent(self, event):
        """
        Record activity on any mouse click in the window
        """
        self.record_activity("Mouse click")
        super().mousePressEvent(event)
    
    def keyPressEvent(self, event):
        """
        Record activity on any key press in the window
        """
        self.record_activity("Key press")
        super().keyPressEvent(event)
    
    def check_all_cameras_completed(self):
        """
        Check if all cameras have 'completedMultiple' status in connection table
        """
        completed_count = 0
        for serial in self.serial_mapping.keys():
            if self.camera_connection_status.get(serial) == "completedMultiple":
                completed_count += 1
        
        return completed_count == 5
    
    def get_todays_directory_path(self):
        """
        Generate today's directory path based on current date
        """
        today = datetime.now().strftime("%Y-%m-%d")
        return f"/Users/signlab/signCollect/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles/{today}/raw"
    
    def count_files_in_directory(self, directory_path=None):
        """
        Count files in the specified directory by camera letter (A, B, L, M, R)
        If no directory specified, use today's date directory
        """
        if not directory_path:
            directory_path = self.get_todays_directory_path()
        
        try:
            if not os.path.exists(directory_path):
                self.log_message(f"Directory does not exist: {directory_path}", 'red')
                return
            
            # Count files by camera letter
            file_counts = {'A': 0, 'B': 0, 'L': 0, 'M': 0, 'R': 0}
            total_files = 0
            
            # List all files in directory
            for filename in os.listdir(directory_path):
                if os.path.isfile(os.path.join(directory_path, filename)):
                    total_files += 1
                    # Check if filename starts with camera letter
                    if len(filename) > 0 and filename[0] in file_counts:
                        file_counts[filename[0]] += 1
            
            # Log the results
            self.log_message(f"Directory file count results for: {directory_path}", 'green')
            self.log_message(f"Total files in directory: {total_files}", 'green')
            
            for camera, count in sorted(file_counts.items()):
                self.log_message(f"Camera {camera} files: {count}", 'green')
            
            # Check if all cameras have equal file counts
            counts = list(file_counts.values())
            if all(count == counts[0] for count in counts) and counts[0] > 0:
                self.log_message(f"✅ All cameras have equal file counts ({counts[0]} files each) - Directory verification successful!", 'green')
            else:
                self.log_message("⚠️ File counts are not equal across cameras in directory:", 'orange')
                for camera, count in sorted(file_counts.items()):
                    self.log_message(f"  Camera {camera}: {count} files", 'orange')
                    
        except Exception as e:
            self.log_message(f"Error counting files in directory: {e}", 'red')
    
    def check_camera_completion_status(self):
        """
        Timer callback to check if all cameras have completed downloads
        """
        all_completed = self.check_all_cameras_completed()
        
        # Only trigger once when all cameras complete
        if all_completed and not self.last_completion_check:
            self.last_completion_check = True
            self.log_message("✅ All cameras show 'Downloaden klaar' status - Starting directory scan", 'green')
            self.count_files_in_directory()
        elif not all_completed:
            # Reset the flag if not all cameras are completed anymore
            self.last_completion_check = False

# ----------------------------
# Step 6: Worker Class for Restarting RemoteCli Independently
# ----------------------------
# Already defined as RemoteCliRestartWorker above.

# ----------------------------
# Step 7: Main Function
# ----------------------------
def main():
    # Path to the RemoteCli program
    remote_cli_path = "/Users/signlab/Desktop/SonyRemote/RemoteCli"

    # Verify if the RemoteCli path exists and is executable
    if not os.path.isfile(remote_cli_path):
        print(f"Error: RemoteCli not found at {remote_cli_path}")
        sys.exit(1)
    if not os.access(remote_cli_path, os.X_OK):
        print(f"Error: RemoteCli at {remote_cli_path} is not executable.")
        sys.exit(1)

    # Create the application
    app = QApplication(sys.argv)

    # Initialize and display the USB Monitor
    monitor = USBMonitor(SERIAL_MAPPING, remote_cli_path)

    # Execute the application
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
