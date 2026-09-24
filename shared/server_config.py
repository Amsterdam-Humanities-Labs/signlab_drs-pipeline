"""Base URL of the SignCollect server that the DRS services talk to.

Set SIGNCOLLECT_URL in the environment or in the untracked .env in the repo
root (see .env.example). Without it, the services use https://signcollect.nl.

VIDEOFIX_TOKEN (same places) is the token videoFix/api.php (the crop-fix queue)
wants in the X-Api-Token header; see videofix_headers().
"""
import os

DEFAULT_SERVER_URL = "https://signcollect.nl"


def _load_env() -> None:
    env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
    try:
        with open(env_file) as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ.setdefault(key.strip(), value.strip().strip('"\''))
    except OSError:
        pass


_load_env()
SERVER_URL = (os.environ.get("SIGNCOLLECT_URL") or DEFAULT_SERVER_URL).rstrip("/")
VIDEOFIX_TOKEN = os.environ.get("VIDEOFIX_TOKEN", "")


def server_url(path: str = "") -> str:
    """SERVER_URL joined with a path, e.g. server_url("videoProc/upload2.php")."""
    return f"{SERVER_URL}/{path.lstrip('/')}" if path else SERVER_URL


def videofix_headers() -> dict:
    """Headers for videoFix/api.php: X-Api-Token from VIDEOFIX_TOKEN, if set."""
    return {"X-Api-Token": VIDEOFIX_TOKEN} if VIDEOFIX_TOKEN else {}
