import json
from pathlib import Path
from jarvis_ingest.state import StateStore, file_hash


def test_file_hash_changes_with_content(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hello")
    h1 = file_hash(f)
    f.write_text("world")
    h2 = file_hash(f)
    assert h1 != h2
    assert len(h1) == 64  # sha256 hex


def test_state_store_tracks_changes(tmp_path):
    state_path = tmp_path / "state.json"
    f = tmp_path / "doc.txt"
    f.write_text("v1")
    store = StateStore(state_path)
    h = store.get_hash(f)
    assert store.is_changed(f, h) is True          # never seen
    store.update(f, h)
    store.save()

    reloaded = StateStore(state_path)
    h = reloaded.get_hash(f)
    assert reloaded.is_changed(f, h) is False       # unchanged since recorded
    f.write_text("v2")
    h = reloaded.get_hash(f)
    assert reloaded.is_changed(f, h) is True         # content changed


def test_state_persisted_as_json(tmp_path):
    state_path = tmp_path / "state.json"
    f = tmp_path / "doc.txt"
    f.write_text("x")
    store = StateStore(state_path)
    store.update(f, store.get_hash(f))
    store.save()
    data = json.loads(state_path.read_text())
    assert str(f.resolve()) in data
