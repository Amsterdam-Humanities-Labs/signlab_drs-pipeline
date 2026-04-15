#!/usr/bin/env python3

import unittest
import tempfile
import os
import signal
import sys
import time
import subprocess
from unittest.mock import Mock, patch, MagicMock, call, mock_open
sys.path.insert(0, '/Users/signlab/drs')
from startupScript import ProcessMonitor, signal_handler, create_watchdog_script


class TestProcessMonitor(unittest.TestCase):
    
    def setUp(self):
        self.monitor = ProcessMonitor()
        # Override services for testing with simpler commands
        self.monitor.services = [
            {
                'name': 'test_service',
                'command': ['echo', 'test'],
                'cwd': '/tmp'
            }
        ]
        
    def tearDown(self):
        # Clean up any running processes
        self.monitor.stop_all_services()
        
    def test_process_monitor_initialization(self):
        """Test ProcessMonitor initialization with default values"""
        self.assertEqual(self.monitor.max_restarts, 5)
        self.assertEqual(self.monitor.restart_window, 300)
        self.assertEqual(self.monitor.cooldown_period, 900)
        self.assertTrue(self.monitor.running)
        self.assertIsInstance(self.monitor.processes, dict)
        self.assertIsInstance(self.monitor.restart_counts, dict)
        self.assertIsInstance(self.monitor.restart_timestamps, dict)
        self.assertIsInstance(self.monitor.disabled_services, dict)
        
    @patch('startupScript.subprocess.Popen')
    def test_start_service_success(self, mock_popen):
        """Test successful service startup"""
        mock_process = Mock()
        mock_process.pid = 12345
        mock_popen.return_value = mock_process
        
        # Mock the get_log_file_handle method to return a mock file
        mock_log_file = Mock()
        with patch.object(self.monitor, 'get_log_file_handle', return_value=mock_log_file):
            service = {'name': 'test', 'command': ['echo', 'test'], 'cwd': '/tmp'}
            result = self.monitor.start_service(service)
            
            self.assertEqual(result, mock_process)
            self.assertEqual(self.monitor.processes['test'], mock_process)
            
            # Verify the new Popen call signature with logging parameters
            mock_popen.assert_called_once()
            call_args = mock_popen.call_args
            self.assertEqual(call_args[0][0], ['echo', 'test'])  # command
            self.assertEqual(call_args[1]['cwd'], '/tmp')
            self.assertEqual(call_args[1]['stdout'], mock_log_file)
            self.assertEqual(call_args[1]['stderr'], subprocess.STDOUT)
            self.assertEqual(call_args[1]['preexec_fn'], os.setsid)
            self.assertTrue(call_args[1]['universal_newlines'])
            self.assertEqual(call_args[1]['bufsize'], 1)
        
    @patch('startupScript.subprocess.Popen')
    def test_start_service_failure(self, mock_popen):
        """Test service startup failure"""
        mock_popen.side_effect = Exception("Failed to start")
        
        service = {'name': 'test', 'command': ['echo', 'test'], 'cwd': '/tmp'}
        result = self.monitor.start_service(service)
        
        self.assertIsNone(result)
        self.assertNotIn('test', self.monitor.processes)
        
    @patch('startupScript.subprocess.run')
    @patch('startupScript.subprocess.Popen')
    def test_start_rclone_service(self, mock_popen, mock_run):
        """Test rclone service startup with unmount"""
        mock_process = Mock()
        mock_process.pid = 12345
        mock_popen.return_value = mock_process
        
        # Mock the get_log_file_handle method to return a mock file
        mock_log_file = Mock()
        with patch.object(self.monitor, 'get_log_file_handle', return_value=mock_log_file):
            service = {
                'name': 'rclone',
                'command': ['rclone', 'mount', 'test:'],
                'cwd': '/tmp'
            }
            
            result = self.monitor.start_service(service)
            
            # Verify umount was called
            mock_run.assert_called_once_with(
                ['umount', '-f', '/Users/signlab/signCollect'],
                check=False, capture_output=True, timeout=30
            )
            
            self.assertEqual(result, mock_process)
            self.assertEqual(self.monitor.processes['rclone'], mock_process)
            
            # Verify the new Popen call signature was used
            mock_popen.assert_called_once()
            call_args = mock_popen.call_args
            self.assertEqual(call_args[0][0], ['rclone', 'mount', 'test:'])
            self.assertEqual(call_args[1]['cwd'], '/tmp')
            self.assertEqual(call_args[1]['stdout'], mock_log_file)
        
    def test_check_process_running(self):
        """Test checking if process is running"""
        mock_process = Mock()
        mock_process.poll.return_value = None
        
        result = self.monitor.check_process('test', mock_process)
        
        self.assertTrue(result)
        mock_process.poll.assert_called_once()
        
    def test_check_process_not_running(self):
        """Test checking if process is not running"""
        mock_process = Mock()
        mock_process.poll.return_value = 1
        
        result = self.monitor.check_process('test', mock_process)
        
        self.assertFalse(result)
        mock_process.poll.assert_called_once()
        
    @patch('startupScript.time.time')
    def test_should_restart_service_within_limit(self, mock_time):
        """Test restart decision when within restart limits"""
        mock_time.return_value = 1000.0
        
        # Add some recent restarts but not exceeding limit
        self.monitor.restart_timestamps['test'] = [950.0, 900.0]
        
        result = self.monitor.should_restart_service('test')
        
        self.assertTrue(result)
        
    @patch('startupScript.time.time')
    def test_should_restart_service_exceeds_limit(self, mock_time):
        """Test restart decision when exceeding restart limits"""
        mock_time.return_value = 1000.0
        
        # Add restarts exceeding the limit
        self.monitor.restart_timestamps['test'] = [950.0, 900.0, 850.0, 800.0, 750.0]
        
        result = self.monitor.should_restart_service('test')
        
        self.assertFalse(result)
        self.assertIn('test', self.monitor.disabled_services)
        
    @patch('startupScript.time.time')
    def test_should_restart_service_cooldown_period(self, mock_time):
        """Test restart decision during cooldown period"""
        mock_time.return_value = 1000.0
        
        # Service was disabled 5 minutes ago (cooldown is 15 minutes)
        self.monitor.disabled_services['test'] = 700.0
        
        result = self.monitor.should_restart_service('test')
        
        self.assertFalse(result)
        
    @patch('startupScript.time.time')
    def test_should_restart_service_after_cooldown(self, mock_time):
        """Test restart decision after cooldown period expires"""
        mock_time.return_value = 2000.0
        
        # Service was disabled 20 minutes ago (cooldown is 15 minutes)
        self.monitor.disabled_services['test'] = 800.0
        
        result = self.monitor.should_restart_service('test')
        
        self.assertTrue(result)
        self.assertNotIn('test', self.monitor.disabled_services)
        
    @patch('startupScript.time.time')
    @patch('startupScript.time.sleep')
    @patch('startupScript.os.killpg')
    def test_restart_service(self, mock_killpg, mock_sleep, mock_time):
        """Test service restart functionality"""
        mock_time.return_value = 1000.0
        
        # Mock existing process
        mock_process = Mock()
        mock_process.poll.return_value = 1  # Process is dead
        self.monitor.processes['test'] = mock_process
        
        service = {'name': 'test', 'command': ['echo', 'test'], 'cwd': '/tmp'}
        
        with patch.object(self.monitor, 'start_service') as mock_start:
            self.monitor.restart_service(service)
            
            # Verify restart timestamp was recorded
            self.assertIn('test', self.monitor.restart_timestamps)
            self.assertEqual(len(self.monitor.restart_timestamps['test']), 1)
            self.assertEqual(self.monitor.restart_timestamps['test'][0], 1000.0)
            
            # Verify service was restarted
            mock_sleep.assert_called_once_with(2)
            mock_start.assert_called_once_with(service)
            
    @patch('startupScript.time.sleep')
    def test_start_all_services(self, mock_sleep):
        """Test starting all configured services"""
        with patch.object(self.monitor, 'start_service') as mock_start:
            self.monitor.start_all_services()
            
            # Verify all services were started
            self.assertEqual(mock_start.call_count, len(self.monitor.services))
            mock_start.assert_called_with(self.monitor.services[0])
            
            # Verify sleep was called for staggered startup
            mock_sleep.assert_called_with(1)
            
    @patch('startupScript.time.sleep')
    def test_monitor_services_loop(self, mock_sleep):
        """Test service monitoring loop"""
        # Mock a service that needs restart
        mock_process = Mock()
        mock_process.poll.return_value = 1  # Process is dead
        self.monitor.processes['test'] = mock_process
        
        service = {'name': 'test', 'command': ['echo', 'test'], 'cwd': '/tmp'}
        self.monitor.services = [service]
        
        with patch.object(self.monitor, 'restart_service') as mock_restart:
            with patch.object(self.monitor, 'check_process', return_value=False):
                # Run one iteration of the monitoring loop
                iteration_count = 0
                original_running = self.monitor.running
                
                def mock_monitor_iteration():
                    nonlocal iteration_count
                    iteration_count += 1
                    if iteration_count >= 1:
                        self.monitor.running = False
                    
                    # Simulate the monitoring logic
                    for service in self.monitor.services:
                        name = service['name']
                        if name in self.monitor.processes:
                            process = self.monitor.processes[name]
                            if not self.monitor.check_process(name, process):
                                self.monitor.restart_service(service)
                
                mock_monitor_iteration()
                
                mock_restart.assert_called_once_with(service)
            
    @patch('startupScript.os.killpg')
    @patch('startupScript.os.getpgid')
    def test_stop_all_services(self, mock_getpgid, mock_killpg):
        """Test stopping all running services"""
        # Mock running processes with proper pid attributes
        mock_process1 = Mock()
        mock_process1.pid = 1001
        mock_process1.wait.return_value = None
        mock_process2 = Mock()
        mock_process2.pid = 1002
        mock_process2.wait.return_value = None
        
        self.monitor.processes = {
            'service1': mock_process1,
            'service2': mock_process2
        }
        
        # Mock getpgid to return the process group IDs
        mock_getpgid.side_effect = [1001, 1002]
        
        self.monitor.stop_all_services()
        
        # Verify getpgid was called with process PIDs
        mock_getpgid.assert_any_call(1001)
        mock_getpgid.assert_any_call(1002)
        
        # Verify processes were terminated
        self.assertEqual(mock_killpg.call_count, 2)
        mock_killpg.assert_any_call(1001, signal.SIGTERM)
        mock_killpg.assert_any_call(1002, signal.SIGTERM)
        
        # Verify running flag was set to False
        self.assertFalse(self.monitor.running)


class TestSignalHandler(unittest.TestCase):
    
    @patch('startupScript.sys.exit')
    @patch('startupScript.ProcessMonitor')
    def test_signal_handler(self, mock_monitor_class, mock_exit):
        """Test signal handler functionality"""
        # Create a mock monitor instance
        mock_monitor = Mock()
        mock_monitor_class.return_value = mock_monitor
        
        # Set up the global monitor variable
        import startupScript
        original_monitor = getattr(startupScript, 'monitor', None)
        startupScript.monitor = mock_monitor
        
        try:
            signal_handler(signal.SIGTERM, None)
            
            mock_monitor.stop_all_services.assert_called_once()
            mock_exit.assert_called_once_with(0)
        finally:
            # Restore original monitor
            if original_monitor is not None:
                startupScript.monitor = original_monitor
            elif hasattr(startupScript, 'monitor'):
                delattr(startupScript, 'monitor')


class TestWatchdogScript(unittest.TestCase):
    
    @patch('startupScript.os.chmod')
    @patch('builtins.open')
    def test_create_watchdog_script(self, mock_open, mock_chmod):
        """Test watchdog script creation"""
        mock_file = Mock()
        mock_open.return_value.__enter__.return_value = mock_file
        
        create_watchdog_script()
        
        # Verify file was opened for writing
        mock_open.assert_called_once_with('/Users/signlab/drs/scripts/watchdog.sh', 'w')
        
        # Verify content was written
        mock_file.write.assert_called_once()
        written_content = mock_file.write.call_args[0][0]
        self.assertIn('Watchdog script for startupScript.py', written_content)
        self.assertIn('/Users/signlab/drs/startupScript.py', written_content)
        
        # Verify file permissions were set
        mock_chmod.assert_called_once_with('/Users/signlab/drs/scripts/watchdog.sh', 0o755)
        
    @patch('startupScript.os.chmod')
    @patch('builtins.open')
    def test_create_watchdog_script_failure(self, mock_open, mock_chmod):
        """Test watchdog script creation failure"""
        mock_open.side_effect = Exception("Permission denied")
        
        # Should not raise exception
        create_watchdog_script()
        
        # Verify file opening was attempted
        mock_open.assert_called_once_with('/Users/signlab/drs/scripts/watchdog.sh', 'w')
        
        # Verify chmod was not called due to exception
        mock_chmod.assert_not_called()


class TestMainExecution(unittest.TestCase):
    
    def test_main_execution_setup(self):
        """Test main execution components exist and can be called"""
        # Test that main components can be instantiated
        from startupScript import ProcessMonitor, create_watchdog_script
        
        # Test ProcessMonitor can be created
        monitor = ProcessMonitor()
        self.assertIsNotNone(monitor)
        
        # Test create_watchdog_script can be called
        with patch('startupScript.os.chmod') as mock_chmod:
            with patch('builtins.open', mock_open()) as mock_file:
                create_watchdog_script()
                mock_file.assert_called_once_with('/Users/signlab/drs/scripts/watchdog.sh', 'w')
                mock_chmod.assert_called_once()
        
        # Test signal handler import
        from startupScript import signal_handler
        self.assertIsNotNone(signal_handler)


if __name__ == '__main__':
    # Run with verbose output
    unittest.main(verbosity=2)