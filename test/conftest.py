"""Make the unit tests runnable off the DRS Mac: put services/, shared/ and the repo
root on sys.path, stub the hardware/network clients, and send the Mac-only log file
of startupScript.py to a temp dir when /Users/signlab/drs does not exist."""
import logging.handlers
import os
import sys
import tempfile
from unittest.mock import MagicMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("", "services", "shared"):
    sys.path.insert(0, os.path.join(ROOT, sub))

for name in ("signcollect_monitor", "python_get_resolve", "video_api_client", "pyautogui"):
    sys.modules.setdefault(name, MagicMock())

if not os.path.isdir("/Users/signlab/drs"):
    _RFH = logging.handlers.RotatingFileHandler
    _tmp = tempfile.mkdtemp(prefix="drs-test-")

    class _TmpRotatingFileHandler(_RFH):
        def __init__(self, filename, *a, **kw):
            if str(filename).startswith("/Users/signlab/"):
                filename = os.path.join(_tmp, os.path.basename(filename))
            super().__init__(filename, *a, **kw)

    logging.handlers.RotatingFileHandler = _TmpRotatingFileHandler
