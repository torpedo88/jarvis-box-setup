from pathlib import Path
import responses
from jarvis_ingest.client import OpenWebUIClient

BASE = "http://owui.test"


@responses.activate
def test_upload_and_attach(tmp_path):
    responses.add(responses.POST, f"{BASE}/api/v1/files/",
                  json={"id": "file-123"}, status=200)
    responses.add(responses.POST, f"{BASE}/api/v1/knowledge/kb-1/file/add",
                  json={"id": "kb-1"}, status=200)

    f = tmp_path / "doc.txt"
    f.write_text("hello")
    client = OpenWebUIClient(BASE, "sk-test")
    file_id = client.upload_file(f)
    assert file_id == "file-123"
    client.attach_file_to_knowledge("kb-1", file_id)

    # auth header sent
    assert responses.calls[0].request.headers["Authorization"] == "Bearer sk-test"
    # attach body references the uploaded file id
    assert "file-123" in responses.calls[1].request.body.decode()


@responses.activate
def test_upload_raises_on_http_error(tmp_path):
    responses.add(responses.POST, f"{BASE}/api/v1/files/", status=500)
    f = tmp_path / "doc.txt"
    f.write_text("x")
    client = OpenWebUIClient(BASE, "sk-test")
    import pytest
    with pytest.raises(Exception):
        client.upload_file(f)
