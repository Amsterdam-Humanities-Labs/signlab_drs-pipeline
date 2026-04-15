#!/usr/bin/env python3
"""
Unit tests for mouse.py
"""

import unittest
import json
import os
import tempfile
from unittest.mock import patch, MagicMock
import sys

# Import the mouse module
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Mock pyautogui before importing mouse
sys.modules['pyautogui'] = MagicMock()

# Create a mock logger before importing
import logging
mock_logger = MagicMock(spec=logging.Logger)

# Patch the logger at module level
with patch('logging.getLogger', return_value=mock_logger):
    import mouse
    mouse.logger = mock_logger


class TestMouseMovement(unittest.TestCase):
    """Test cases for mouse movement script."""
    
    def setUp(self):
        """Set up test environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_file = os.path.join(self.temp_dir, "test_config.json")
        
    def tearDown(self):
        """Clean up test environment."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        
    def test_load_config_defaults(self):
        """Test loading default configuration."""
        with patch('mouse.CONFIG_FILE', 'nonexistent.json'):
            config = mouse.load_config()
            self.assertEqual(config['interval'], mouse.DEFAULT_INTERVAL)
            self.assertEqual(config['movement_range'], mouse.DEFAULT_MOVEMENT_RANGE)
            self.assertEqual(config['movement_duration'], mouse.DEFAULT_MOVEMENT_DURATION)
    
    def test_load_config_from_file(self):
        """Test loading configuration from file."""
        test_config = {
            'interval': 30,
            'movement_range': 100,
            'movement_duration': 0.5
        }
        with open(self.config_file, 'w') as f:
            json.dump(test_config, f)
            
        with patch('mouse.CONFIG_FILE', self.config_file):
            config = mouse.load_config()
            self.assertEqual(config['interval'], 30)
            self.assertEqual(config['movement_range'], 100)
            self.assertEqual(config['movement_duration'], 0.5)
    
    def test_load_config_env_override(self):
        """Test environment variable override."""
        with patch.dict(os.environ, {
            'MOUSE_INTERVAL': '45',
            'MOUSE_MOVEMENT_RANGE': '75',
            'MOUSE_MOVEMENT_DURATION': '0.2'
        }):
            config = mouse.load_config()
            self.assertEqual(config['interval'], 45)
            self.assertEqual(config['movement_range'], 75)
            self.assertEqual(config['movement_duration'], 0.2)
    
    @patch('pyautogui.size')
    def test_get_screen_center_success(self, mock_size):
        """Test successful screen center calculation."""
        mock_size.return_value = (1920, 1080)
        center_x, center_y = mouse.get_screen_center()
        self.assertEqual(center_x, 960)
        self.assertEqual(center_y, 540)
    
    @patch('pyautogui.size')
    def test_get_screen_center_failure(self, mock_size):
        """Test screen center with error handling."""
        mock_size.side_effect = Exception("Display error")
        center_x, center_y = mouse.get_screen_center()
        # Should return default values
        self.assertEqual(center_x, 960)
        self.assertEqual(center_y, 540)
    
    @patch('pyautogui.moveTo')
    def test_move_mouse_safely_success(self, mock_moveto):
        """Test successful mouse movement."""
        result = mouse.move_mouse_safely(100, 100, 0.1)
        self.assertTrue(result)
        mock_moveto.assert_called_once_with(100, 100, duration=0.1)
    
    @patch('pyautogui.moveTo')
    @patch('time.sleep')
    def test_move_mouse_safely_retry(self, mock_sleep, mock_moveto):
        """Test mouse movement with retry."""
        mock_moveto.side_effect = [Exception("Error"), None]
        result = mouse.move_mouse_safely(100, 100, 0.1)
        self.assertTrue(result)
        self.assertEqual(mock_moveto.call_count, 2)
        mock_sleep.assert_called_once()
    
    @patch('pyautogui.position')
    def test_perform_health_check_success(self, mock_position):
        """Test successful health check."""
        mock_position.return_value = (500, 500)
        result = mouse.perform_health_check()
        self.assertTrue(result)
    
    @patch('pyautogui.position')
    def test_perform_health_check_failure(self, mock_position):
        """Test failed health check."""
        mock_position.side_effect = Exception("Connection lost")
        result = mouse.perform_health_check()
        self.assertFalse(result)
    
    def test_signal_handler(self):
        """Test signal handler sets shutdown flag."""
        # Reset global flag
        mouse.shutdown_requested = False
        # Call signal handler
        mouse.signal_handler(15, None)
        # Check flag is set
        self.assertTrue(mouse.shutdown_requested)
        # Reset for other tests
        mouse.shutdown_requested = False


if __name__ == '__main__':
    unittest.main()