"""Old name of crop_sentences.py ("znn" = "zin", sentence in NGT). Kept so existing
commands and imports keep working; runs / re-exports crop_sentences.py."""
import os
import runpy
import sys

_here = os.path.dirname(os.path.abspath(__file__))

if __name__ == "__main__":
    runpy.run_path(os.path.join(_here, "crop_sentences.py"), run_name="__main__")
else:
    sys.path.insert(0, _here)
    from crop_sentences import *  # noqa: F401,F403
