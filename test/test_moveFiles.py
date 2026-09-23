import unittest
import os
import tempfile
import shutil
from unittest.mock import patch, MagicMock
from pathlib import Path
import sys

# services/ is put on sys.path by conftest.py

from moveFiles import convert_date_format, extract_date_from_filename, move_files

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
    
    SOURCE = "/Volumes/cacheDisk/fx30_staging"

    def _patch_fs(self, walk):
        """Staging exists, targets do not, mount is healthy, files are old and non-empty."""
        patches = [
            patch('moveFiles.os.path.exists', side_effect=lambda p: p == self.SOURCE),
            patch('moveFiles.os.walk', return_value=walk),
            patch('moveFiles.os.makedirs'),
            patch('moveFiles.os.path.getsize', return_value=100),
            patch('moveFiles.os.path.getmtime', return_value=0),
            patch('moveFiles.check_mount_health', return_value=True),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    @patch('moveFiles.rsync_copy')
    def test_move_files_success(self, mock_copy):
        self._patch_fs([
            (self.SOURCE + "/2025-05-27/raw", [], ["R20250527_1851.MP4", "L20250527_1852.MP4"]),
            (self.SOURCE + "/2024-01-01/raw", [], ["M20240101_0000.MP4", "invalid_file.MP4"]),
            (self.SOURCE + "/2024-01-01/other", [], ["M20240101_0001.MP4"]),
        ])

        with patch('builtins.print') as mock_print:
            move_files()

        # Only files in raw/ folders with a date in the name are copied
        self.assertEqual(mock_copy.call_count, 3)
        print_calls = [c[0][0] if c[0] else '' for c in mock_print.call_args_list]
        self.assertEqual(len([m for m in print_calls if "Copied:" in str(m)]), 3)

    @patch('moveFiles.os.path.exists', return_value=False)
    def test_move_files_source_not_found(self, mock_exists):
        with patch('builtins.print') as mock_print:
            move_files()

        mock_print.assert_called_with("Source directory not found: /Volumes/cacheDisk/fx30_staging")

    @patch('moveFiles.rsync_copy', side_effect=Exception("Permission denied"))
    def test_move_files_with_errors(self, mock_copy):
        self._patch_fs([(self.SOURCE + "/2025-05-27/raw", [], ["R20250527_1851.MP4"])])

        with patch('builtins.print') as mock_print:
            move_files()

        print_calls = [c[0][0] if c[0] else '' for c in mock_print.call_args_list]
        self.assertEqual(len([m for m in print_calls if "Unexpected error copying" in str(m)]), 1)
        summary = [m for m in print_calls if "Summary:" in str(m)]
        self.assertEqual(len(summary), 1)
        self.assertIn("1 errors", summary[0])

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