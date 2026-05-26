from pathlib import Path
from typing import Iterable, Iterator, Optional

DEFAULT_EXTENSIONS = {".txt", ".md", ".markdown", ".pdf", ".docx", ".csv", ".json"}


class FolderAdapter:
    """Yields ingestible files under a root directory (recursive)."""

    def __init__(self, root: Path, extensions: Optional[Iterable[str]] = None):
        self.root = Path(root)
        self.extensions = set(extensions) if extensions else set(DEFAULT_EXTENSIONS)

    def iter_files(self) -> Iterator[Path]:
        for path in sorted(self.root.rglob("*")):
            if path.is_file() and path.suffix.lower() in self.extensions:
                yield path
