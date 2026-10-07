"""videoProc/upload2.php answers in plain text; see shared/upload_reply.py."""
import pytest

from upload_reply import parse_upload_reply


def test_plain_text_success():
    r = parse_upload_reply("video", 200, "Video uploaded successfully.\nNo thumbnail file uploaded.\n")
    assert r["success"] is True
    assert parse_upload_reply("thumbnail", 200, "No video file uploaded.\nThumbnail uploaded successfully.\n")["success"]


def test_json_success_still_works():
    assert parse_upload_reply("video", 200, '{"success": true, "file": "M1.mp4"}')["file"] == "M1.mp4"


@pytest.mark.parametrize("status,text", [
    (200, "Failed to upload video: Could not move file.\n"),
    (200, "No video file uploaded.\nThumbnail uploaded successfully.\n"),   # the other field
    (200, '{"success": false, "error": "disk full"}'),
    (500, ""),
    (200, "<html>502 Bad Gateway</html>"),
])
def test_failures_raise_with_the_servers_words(status, text):
    with pytest.raises(RuntimeError):
        parse_upload_reply("video", status, text)
