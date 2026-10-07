"""What videoProc/upload2.php answered.

The endpoint replies in plain text ("Video uploaded successfully."), not JSON.
Reading it with response.json() made every upload look failed: the log filled
with "Expecting value" and the thumbnail that follows a video was never sent.
"""
import json


def parse_upload_reply(field, status_code, text):
    """Return a dict for a successful upload of `field` ('video' or
    'thumbnail'); raise RuntimeError with the server's words otherwise."""
    text = (text or "").strip()
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, dict):
        if data.get("success") is False:
            raise RuntimeError(f"server refused the {field}: {text[:200]}")
        return data
    if status_code == 200 and f"{field.capitalize()} uploaded successfully" in text:
        return {"success": True, "message": text}
    raise RuntimeError(f"server said: {text[:200] or 'HTTP %s' % status_code}")
