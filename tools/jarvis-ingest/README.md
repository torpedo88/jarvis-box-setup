# jarvis-ingest

Pushes files into the Open WebUI **Knowledge** base that backs the "Jarvis"
model, so Jarvis can answer from your documents (RAG). Embeddings are computed
on CPU (`BAAI/bge-small-en-v1.5`) and stored in Qdrant; the GPU stays dedicated
to text generation.

## Setup

    python3 -m venv .venv && . .venv/bin/activate
    pip install -r requirements.txt

## Usage

    export JARVIS_BASE_URL=http://localhost:3000
    export JARVIS_API_KEY=sk-...           # Open WebUI -> Settings -> Account -> API Keys
    python -m jarvis_ingest.cli \
      --source /path/to/docs \
      --knowledge-id <jarvis-kb id> \      # from POST /api/v1/knowledge/create
      --state ~/.jarvis-ingest-state.json

Re-runs skip unchanged files (tracked by SHA-256 content hash in the `--state`
file). Unreadable / failed files are logged and retried on the next run.

Default ingested extensions: `.txt .md .markdown .pdf .docx .csv .json`.

## How Jarvis uses the knowledge (RAG)

- **In the Open WebUI chat (phone/tablet/computer):** just select the **Jarvis**
  model. Its attached knowledge collection is sent automatically, so answers are
  grounded and cited. This is the normal way to use it.
- **Via the raw API** (`POST /api/chat/completions`): the model's attached
  knowledge is NOT auto-applied on raw calls. You must pass a `chat_id` and the
  collection explicitly:

      {"model":"jarvis","chat_id":"my-session",
       "files":[{"type":"collection","id":"<jarvis-kb id>"}],
       "messages":[{"role":"user","content":"..."}],"stream":false}

## Known limitations

- **Duplicate content:** Open WebUI rejects adding a file whose content already
  exists in the collection with HTTP 400. The tool logs this as a failure and
  will retry it on each run (it never records the file in state). If you ingest
  the same content through another path (e.g. the UI), expect repeated 400s for
  that file. Treating dup-400 as success is a possible future enhancement.
- v1 ships a single source adapter: local folders.

## Tests

    pytest -q

## Extending sources

Add a new adapter class in `jarvis_ingest/adapters.py` exposing `iter_files()`
(yielding `Path` objects); the orchestrator and CLI consume any adapter with
that interface. No changes to the client/orchestrator needed.
