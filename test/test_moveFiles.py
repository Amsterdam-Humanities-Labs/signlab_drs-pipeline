import unittest
import os
import tempfile
import shutil
from unittest.mock import patch, MagicMock
from pathlib import Path
import sys

# Add the parent directory to the path to import moveFiles_backup
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Import the functions from moveFiles_backup
from moveFiles_backup import convert_date_format, extract_date_from_filename, move_files

class TestMoveFiles(unittest.TestCase):
    
    def test_convert_date_format(self):
        # Test valid date conversion
        self.assertEqual(convert_date_format("20250527"), "2025-05-27")
        self.assertEqual(convert_date_format("20240101"), "2024-01-01")
        self.assertEqual(convert_date_format("20231231"), "2023-12-31")
        
        # Test invalid input
        self.assertIsNone(convert_date_format("2025052"))  # Too short
        self.assertIsNone(convert_date_format("202505271"))  # Too long
        self.assertIsNone(convert_date_format(""))  # Empty string
        self.assertIsNone(convert_date_format("invalid"))  # Non-numeric
    
    def test_extract_date_from_filename(self):
        # Test valid filename patterns
        self.assertEqual(extract_date_from_filename("R20250527_1851.MP4"), "2025-05-27")
        self.assertEqual(extract_date_from_filename("L20240101_0000.MP4"), "2024-01-01")
        self.assertEqual(extract_date_from_filename("M20231231_2359.MP4"), "2023-12-31")
        self.assertEqual(extract_date_from_filename("A20220615_1234.MP4"), "2022-06-15")
        self.assertEqual(extract_date_from_filename("B20210708_0930.MP4"), "2021-07-08")
        
        # Test invalid filename patterns
        self.assertIsNone(extract_date_from_filename("invalid_filename.MP4"))
        self.assertIsNone(extract_date_from_filename("20250527_1851.MP4"))  # Missing camera prefix
        self.assertIsNone(extract_date_from_filename("R2025052_1851.MP4"))  # Invalid date format
        self.assertIsNone(extract_date_from_filename("R20250527.MP4"))  # Missing time
        self.assertIsNone(extract_date_from_filename(""))  # Empty string
    
    def setUp(self):
        # Create temporary directories for testing
        self.temp_dir = tempfile.mkdtemp()
        self.source_dir = os.path.join(self.temp_dir, "source")
        self.target_dir = os.path.join(self.temp_dir, "target")
        
        # Create source directory structure
        os.makedirs(os.path.join(self.source_dir, "2025-05-27", "raw"), exist_ok=True)
        os.makedirs(os.path.join(self.source_dir, "2024-01-01", "raw"), exist_ok=True)
        
        # Create test files
        self.test_files = [
            "R20250527_1851.MP4",
            "L20250527_1852.MP4", 
            "M20240101_0000.MP4",
            "invalid_file.MP4"
        ]
        
        for filename in self.test_files:
            file_path = os.path.join(self.source_dir, "2025-05-27", "raw", filename)
            if "20240101" in filename:
                file_path = os.path.join(self.source_dir, "2024-01-01", "raw", filename)
            
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, 'w') as f:
                f.write("test video content")
    
    def tearDown(self):
        # Clean up temporary directories
        shutil.rmtree(self.temp_dir)
    
    @patch('moveFiles_backup.os.path.exists')
    @patch('moveFiles_backup.os.walk')
    @patch('moveFiles_backup.os.makedirs')
    @patch('moveFiles_backup.shutil.move')
    def test_move_files_success(self, mock_move, mock_makedirs, mock_walk, mock_exists):
        # Mock the source directory exists
        mock_exists.return_value = True
        
        # Mock os.walk to return our test files
        mock_walk.return_value = [
            ("/Volumes/cacheDisk/signCollect/studioFiles/2025-05-27/raw", [], ["R20250527_1851.MP4", "L20250527_1852.MP4"]),
            ("/Volumes/cacheDisk/signCollect/studioFiles/2024-01-01/raw", [], ["M20240101_0000.MP4", "invalid_file.MP4"])
        ]
        
        # Mock successful move operations
        mock_move.return_value = None
        
        # Capture print output
        with patch('builtins.print') as mock_print:
            move_files()
        
        # Verify that move was called for valid files (3 files with valid date patterns)
        self.assertEqual(mock_move.call_count, 3)  # R20250527, L20250527, M20240101
        
        # Verify makedirs was called
        self.assertTrue(mock_makedirs.called)
        
        # Verify success messages were printed
        print_calls = [call[0][0] if call[0] else '' for call in mock_print.call_calls]
        success_messages = [msg for msg in print_calls if "Moved:" in str(msg)]
        self.assertEqual(len(success_messages), 3)
    
    @patch('moveFiles_backup.os.path.exists')
    def test_move_files_source_not_found(self, mock_exists):
        # Mock source directory doesn't exist
        mock_exists.return_value = False
        
        with patch('builtins.print') as mock_print:
            move_files()
        
        # Verify error message was printed
        mock_print.assert_called_with("Source directory not found: /Volumes/cacheDisk/signCollect/studioFiles")
    
    @patch('moveFiles_backup.os.path.exists')
    @patch('moveFiles_backup.os.walk')
    @patch('moveFiles_backup.os.makedirs')
    @patch('moveFiles_backup.shutil.move')
    def test_move_files_with_errors(self, mock_move, mock_makedirs, mock_walk, mock_exists):
        # Mock the source directory exists
        mock_exists.return_value = True
        
        # Mock os.walk to return test files
        mock_walk.return_value = [
            ("/Volumes/cacheDisk/signCollect/studioFiles/2025-05-27/raw", [], ["R20250527_1851.MP4"])
        ]
        
        # Mock move operation to raise an exception
        mock_move.side_effect = Exception("Permission denied")
        
        with patch('builtins.print') as mock_print:
            move_files()
        
        # Verify error message was printed
        print_calls = [call[0][0] if call[0] else '' for call in mock_print.call_calls]
        error_messages = [msg for msg in print_calls if "Error moving" in str(msg)]
        self.assertEqual(len(error_messages), 1)
        
        # Verify summary shows 1 error
        summary_messages = [msg for msg in print_calls if "Summary:" in str(msg)]
        self.assertEqual(len(summary_messages), 1)
        self.assertIn("1 errors", summary_messages[0])

class TestMoveFilesIntegration(unittest.TestCase):
    
    def setUp(self):
        # Create a more comprehensive test environment
        self.temp_dir = tempfile.mkdtemp()
        self.source_base = os.path.join(self.temp_dir, "source")
        self.target_base = os.path.join(self.temp_dir, "target")
        
        # Create source directory with realistic structure
        raw_dirs = [
            os.path.join(self.source_base, "2025-05-27", "raw"),
            os.path.join(self.source_base, "2024-01-01", "raw"),
            os.path.join(self.source_base, "2023-12-31", "raw")
        ]
        
        for raw_dir in raw_dirs:
            os.makedirs(raw_dir, exist_ok=True)
        
        # Create test files with various patterns
        test_files = [
            ("2025-05-27", ["R20250527_1851.MP4", "L20250527_1852.MP4", "M20250527_1853.MP4"]),
            ("2024-01-01", ["A20240101_0000.MP4", "B20240101_0001.MP4"]),
            ("2023-12-31", ["invalid_file.MP4", "R20231231_2359.MP4"])
        ]
        
        for date_folder, files in test_files:
            for filename in files:
                file_path = os.path.join(self.source_base, date_folder, "raw", filename)
                with open(file_path, 'w') as f:
                    f.write(f"test content for {filename}")
    
    def tearDown(self):
        shutil.rmtree(self.temp_dir)
    
    def test_integration_file_movement(self):
        # This test would require modifying the original code to make paths configurable
        # For now, we'll test the individual functions thoroughly
        pass

if __name__ == '__main__':
    unittest.main()