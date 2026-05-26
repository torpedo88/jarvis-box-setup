from pathlib import Path
from jarvis_ingest.adapters import FolderAdapter


def test_folder_adapter_yields_supported_files(tmp_path):
    (tmp_path / "keep.txt").write_text("a")
    (tmp_path / "note.md").write_text("b")
    (tmp_path / "skip.bin").write_bytes(b"\x00\x01")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "deep.pdf").write_text("c")

    adapter = FolderAdapter(tmp_path, extensions={".txt", ".md", ".pdf"})
    found = sorted(p.name for p in adapter.iter_files())
    assert found == ["deep.pdf", "keep.txt", "note.md"]  # .bin excluded, recursive


def test_folder_adapter_default_extensions(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "b.docx").write_text("y")
    adapter = FolderAdapter(tmp_path)
    names = sorted(p.name for p in adapter.iter_files())
    assert "a.txt" in names and "b.docx" in names
