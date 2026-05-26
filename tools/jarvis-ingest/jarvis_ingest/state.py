import hashlib
import json
from pathlib import Path


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class StateStore:
    """Maps absolute file path -> last-indexed content hash."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._data: dict[str, str] = {}
        if self.path.exists():
            self._data = json.loads(self.path.read_text())

    def get_hash(self, file_path: Path) -> str:
        return file_hash(Path(file_path))

    def is_changed(self, file_path: Path, current_hash: str) -> bool:
        key = str(Path(file_path).resolve())
        return self._data.get(key) != current_hash

    def update(self, file_path: Path, current_hash: str) -> None:
        key = str(Path(file_path).resolve())
        self._data[key] = current_hash

    def save(self) -> None:
        self.path.write_text(json.dumps(self._data, indent=2))
