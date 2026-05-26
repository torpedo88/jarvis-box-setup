import argparse
import logging
import os
from pathlib import Path

from .adapters import FolderAdapter
from .client import OpenWebUIClient
from .ingest import ingest
from .state import StateStore


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Ingest files into Jarvis knowledge base.")
    p.add_argument("--source", required=True, help="root folder to ingest")
    p.add_argument("--knowledge-id", required=True, help="Open WebUI knowledge id")
    p.add_argument("--base-url", default=os.environ.get("JARVIS_BASE_URL", "http://localhost:3000"))
    p.add_argument("--api-key", default=os.environ.get("JARVIS_API_KEY", ""))
    p.add_argument("--state", default=str(Path.home() / ".jarvis-ingest-state.json"))
    return p


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    if not args.api_key:
        raise SystemExit("Missing API key: pass --api-key or set JARVIS_API_KEY")
    adapter = FolderAdapter(Path(args.source))
    client = OpenWebUIClient(args.base_url, args.api_key)
    state = StateStore(Path(args.state))
    summary = ingest(adapter, client, state, args.knowledge_id)
    logging.info("done: %s", summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
