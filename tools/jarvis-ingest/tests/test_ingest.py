from pathlib import Path
from jarvis_ingest.ingest import ingest
from jarvis_ingest.state import StateStore
from jarvis_ingest.adapters import FolderAdapter


class FakeClient:
    def __init__(self, fail_paths=None):
        self.uploaded = []
        self.attached = []
        self.fail_paths = set(fail_paths or [])
        self._n = 0

    def upload_file(self, path):
        if path.name in self.fail_paths:
            raise RuntimeError("boom")
        self._n += 1
        fid = f"file-{self._n}"
        self.uploaded.append(path.name)
        return fid

    def attach_file_to_knowledge(self, kb_id, file_id):
        self.attached.append((kb_id, file_id))


def test_ingest_uploads_new_files_and_skips_unchanged(tmp_path):
    (tmp_path / "a.txt").write_text("1")
    (tmp_path / "b.md").write_text("2")
    state = StateStore(tmp_path / "state.json")
    adapter = FolderAdapter(tmp_path, extensions={".txt", ".md"})
    client = FakeClient()

    summary = ingest(adapter, client, state, "kb-1")
    assert summary["uploaded"] == 2
    assert summary["skipped"] == 0
    assert sorted(client.uploaded) == ["a.txt", "b.md"]

    # second run: nothing changed -> all skipped
    summary2 = ingest(adapter, client, state, "kb-1")
    assert summary2["uploaded"] == 0
    assert summary2["skipped"] == 2


def test_ingest_skips_bad_file_without_crashing(tmp_path):
    (tmp_path / "good.txt").write_text("1")
    (tmp_path / "bad.txt").write_text("2")
    state = StateStore(tmp_path / "state.json")
    adapter = FolderAdapter(tmp_path, extensions={".txt"})
    client = FakeClient(fail_paths={"bad.txt"})

    summary = ingest(adapter, client, state, "kb-1")
    assert summary["uploaded"] == 1
    assert summary["failed"] == 1
    assert "good.txt" in client.uploaded
    # failed file is NOT recorded as indexed, so it retries next run
    bad = tmp_path / "bad.txt"
    assert state.is_changed(bad, state.get_hash(bad)) is True


def test_ingest_retries_transient_upload_failure(tmp_path):
    (tmp_path / "a.txt").write_text("1")
    state = StateStore(tmp_path / "state.json")
    adapter = FolderAdapter(tmp_path, extensions={".txt"})

    class FlakyClient:
        def __init__(self):
            self.calls = 0
            self.attached = []
        def upload_file(self, path):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("transient")
            return "file-1"
        def attach_file_to_knowledge(self, kb_id, file_id):
            self.attached.append((kb_id, file_id))

    client = FlakyClient()
    summary = ingest(adapter, client, state, "kb-1", retries=2)
    assert summary["uploaded"] == 1
    assert summary["failed"] == 0
    assert client.calls == 2  # failed once, retried, succeeded
