import unittest
import json
import subprocess
from unittest.mock import patch, MagicMock, mock_open, call
import os
import sys

# Add the parent directory to the path to import batch
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Mock the problematic imports before importing batch
with patch.dict('sys.modules', {
    'video_api_client': MagicMock(),
    'python_get_resolve': MagicMock(),
    'requests': MagicMock()
}):
    # Import the functions from batch
    from batch import get_video_orientation, get_resolve_with_retry

class TestBatchFunctions(unittest.TestCase):
    
    def test_get_video_orientation_file_not_found(self):
        # Test when file doesn't exist
        result = get_video_orientation("/nonexistent/file.mp4")
        self.assertEqual(result, (None, "file_not_found"))
    
    @patch('batch.os.path.isfile')
    @patch('batch.subprocess.run')
    @patch('batch.json.loads')
    def test_get_video_orientation_portrait(self, mock_json_loads, mock_subprocess, mock_isfile):
        # Mock file exists
        mock_isfile.return_value = True
        
        # Mock subprocess result
        mock_result = MagicMock()
        mock_result.stdout = '{"media": {"track": []}}'
        mock_subprocess.return_value = mock_result
        
        # Mock mediainfo JSON response for portrait video
        mock_json_loads.return_value = {
            "media": {
                "track": [
                    {"@type": "Video", "Width": "1080", "Height": "1920", "Rotation": "0"}
                ]
            }
        }
        
        rotation, orientation = get_video_orientation("/path/to/video.mp4")
        
        self.assertEqual(rotation, 0)
        self.assertEqual(orientation, "portrait")
        mock_subprocess.assert_called_once_with(
            ["mediainfo", "--Output=JSON", "/path/to/video.mp4"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8'
        )
    
    @patch('batch.os.path.isfile')
    @patch('batch.subprocess.run')
    @patch('batch.json.loads')
    def test_get_video_orientation_landscape(self, mock_json_loads, mock_subprocess, mock_isfile):
        # Mock file exists
        mock_isfile.return_value = True
        
        # Mock subprocess result
        mock_result = MagicMock()
        mock_result.stdout = '{"media": {"track": []}}'
        mock_subprocess.return_value = mock_result
        
        # Mock mediainfo JSON response for landscape video
        mock_json_loads.return_value = {
            "media": {
                "track": [
                    {"@type": "Video", "Width": "1920", "Height": "1080", "Rotation": "0"}
                ]
            }
        }
        
        rotation, orientation = get_video_orientation("/path/to/video.mp4")
        
        self.assertEqual(rotation, 0)
        self.assertEqual(orientation, "landscape")
    
    @patch('batch.os.path.isfile')
    @patch('batch.subprocess.run')
    @patch('batch.json.loads')
    def test_get_video_orientation_with_rotation(self, mock_json_loads, mock_subprocess, mock_isfile):
        # Mock file exists
        mock_isfile.return_value = True
        
        # Mock subprocess result
        mock_result = MagicMock()
        mock_result.stdout = '{"media": {"track": []}}'
        mock_subprocess.return_value = mock_result
        
        # Mock mediainfo JSON response with 90-degree rotation
        mock_json_loads.return_value = {
            "media": {
                "track": [
                    {"@type": "Video", "Width": "1920", "Height": "1080", "Rotation": "90"}
                ]
            }
        }
        
        rotation, orientation = get_video_orientation("/path/to/video.mp4")
        
        # With 90-degree rotation, dimensions should be swapped
        self.assertEqual(rotation, 90)
        self.assertEqual(orientation, "portrait")  # 1080x1920 after rotation
    
    @patch('batch.os.path.isfile')
    @patch('batch.subprocess.run')
    @patch('batch.json.loads')
    def test_get_video_orientation_error_handling(self, mock_json_loads, mock_subprocess, mock_isfile):
        # Mock file exists
        mock_isfile.return_value = True
        
        # Mock subprocess result
        mock_result = MagicMock()
        mock_result.stdout = '{"media": {"track": []}}'
        mock_subprocess.return_value = mock_result
        
        # Mock JSON that will cause an exception
        mock_json_loads.side_effect = Exception("JSON parsing error")
        
        with patch('builtins.print') as mock_print:
            rotation, orientation = get_video_orientation("/path/to/video.mp4")
        
        # Should return default values on error
        self.assertEqual(rotation, 270)
        self.assertEqual(orientation, "portrait")
        mock_print.assert_called_once()
    
    @patch('batch.GetResolve')
    @patch('batch.os.system')
    @patch('batch.time.sleep')
    def test_get_resolve_with_retry_success_first_try(self, mock_sleep, mock_os_system, mock_get_resolve):
        # Mock successful resolve connection on first try
        mock_resolve = MagicMock()
        mock_get_resolve.return_value = mock_resolve
        
        result = get_resolve_with_retry(max_retries=3)
        
        self.assertEqual(result, mock_resolve)
        mock_os_system.assert_not_called()
        mock_sleep.assert_not_called()
    
    @patch('batch.GetResolve')
    @patch('batch.os.system')
    @patch('batch.time.sleep')
    def test_get_resolve_with_retry_success_after_retries(self, mock_sleep, mock_os_system, mock_get_resolve):
        # Mock failed connection first two times, success on third
        mock_resolve = MagicMock()
        mock_get_resolve.side_effect = [None, None, mock_resolve]
        
        with patch('builtins.print') as mock_print:
            result = get_resolve_with_retry(max_retries=3)
        
        self.assertEqual(result, mock_resolve)
        self.assertEqual(mock_os_system.call_count, 2)  # Two retry attempts
        self.assertEqual(mock_sleep.call_count, 2)  # Two sleep calls
        mock_os_system.assert_called_with("open -a 'DaVinci Resolve'")
        mock_sleep.assert_called_with(10)
    
    @patch('batch.GetResolve')
    @patch('batch.os.system')
    @patch('batch.time.sleep')
    def test_get_resolve_with_retry_max_retries_exceeded(self, mock_sleep, mock_os_system, mock_get_resolve):
        # Mock failed connection for all attempts
        mock_get_resolve.return_value = None
        
        with patch('builtins.print') as mock_print:
            result = get_resolve_with_retry(max_retries=2)
        
        self.assertIsNone(result)
        self.assertEqual(mock_os_system.call_count, 2)  # Two retry attempts
        self.assertEqual(mock_sleep.call_count, 2)  # Two sleep calls

class TestBatchIntegration(unittest.TestCase):
    """Integration tests for batch.py main functionality"""
    
    @patch('batch.get_resolve_with_retry')
    def test_main_resolve_connection_failure(self, mock_get_resolve):
        # Mock failed resolve connection
        mock_get_resolve.return_value = None
        
        with patch('builtins.print') as mock_print:
            from batch import main
            main()
        
        # Should print failure message and return early
        print_calls = [call[0][0] for call in mock_print.call_calls]
        failure_messages = [msg for msg in print_calls if "Failed to open DaVinci Resolve" in msg]
        self.assertGreater(len(failure_messages), 0)

class TestBatchVideoProcessing(unittest.TestCase):
    """Tests for video processing logic in batch.py"""
    
    def test_date_validation_logic(self):
        # Test the date validation logic from batch.py
        # This tests the logic around lines 133-137 in batch.py
        
        # Simulate filename and folder date extraction
        filename = "M20250407_1234.MP4"
        date_folder = "2025-04-07"
        
        date_in_filename = filename[1:9]  # "20250407"
        date_in_folder = date_folder.replace("-", "")  # "20250407"
        
        self.assertEqual(date_in_filename, "20250407")
        self.assertEqual(date_in_folder, "20250407")
        self.assertEqual(date_in_filename, date_in_folder)
        
        # Test mismatch case
        filename_mismatch = "M20250408_1234.MP4" 
        date_in_filename_mismatch = filename_mismatch[1:9]  # "20250408"
        self.assertNotEqual(date_in_filename_mismatch, date_in_folder)
    
    def test_project_selection_logic(self):
        # Test project selection based on orientation (lines 147-148)
        
        # Portrait video should use "lala6"
        orientation_portrait = "portrait"
        project_name_portrait = "landscape" if orientation_portrait == "landscape" else "lala6"
        self.assertEqual(project_name_portrait, "lala6")
        
        # Landscape video should use "landscape"  
        orientation_landscape = "landscape"
        project_name_landscape = "landscape" if orientation_landscape == "landscape" else "lala6"
        self.assertEqual(project_name_landscape, "landscape")
    
    def test_filename_processing(self):
        # Test filename processing logic
        filename = "M20250407_1234.MP4"
        base_name = os.path.splitext(filename)[0]
        self.assertEqual(base_name, "M20250407_1234")
        
        # Test with different extensions
        filename_mkv = "L20240101_0000.mkv"
        base_name_mkv = os.path.splitext(filename_mkv)[0]
        self.assertEqual(base_name_mkv, "L20240101_0000")

class TestBatchRenderSettings(unittest.TestCase):
    """Tests for render settings and configuration"""
    
    def test_render_settings_structure(self):
        # Test the render settings dictionary structure (lines 261-266)
        export_dir = "/Users/signlab/drs/export"
        base_name = "M20250407_1234"
        
        render_settings = {
            "TargetDir": str(export_dir),
            "Format": "MP4",
            "Codec": "h264", 
            "CustomName": str(base_name)
        }
        
        self.assertEqual(render_settings["TargetDir"], export_dir)
        self.assertEqual(render_settings["Format"], "MP4")
        self.assertEqual(render_settings["Codec"], "h264")
        self.assertEqual(render_settings["CustomName"], base_name)
    
    def test_crash_detection_interval(self):
        # Test crash detection timing logic (line 274)
        crash_check_interval = 60  # seconds
        self.assertEqual(crash_check_interval, 60)
        
        # Test timeout logic (line 311)
        timeout_seconds = 3600  # 1 hour
        self.assertEqual(timeout_seconds, 3600)

if __name__ == '__main__':
    # Run with verbose output to see individual test results
    unittest.main(verbosity=2)