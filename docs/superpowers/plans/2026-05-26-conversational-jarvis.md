# Conversational Jarvis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the existing Open WebUI + Ollama + Qdrant box into "Jarvis" — a butler-persona chat assistant that remembers the user, answers from their documents (RAG), and ingests thousands of files over time.

**Architecture:** Configuration changes to the running Open WebUI stack (vector DB → Qdrant, CPU embeddings, a public "Jarvis" workspace model with butler prompt + knowledge collection, per-user Memory) plus a standalone Python ingestion tool that pushes files into the Open WebUI Knowledge API. Embeddings run on CPU so the 6GB GPU stays dedicated to text generation at ctx 4096.

**Tech Stack:** Docker Compose, Open WebUI (`ghcr.io/open-webui/open-webui:main`), Ollama (`hermes3:8b`), Qdrant, Python 3 + `requests` + `pytest` for the ingestion tool.

---

## Conventions used in this plan

- The box user is `alienware`. The stack lives at `/opt/stacks/ai/`.
- Open WebUI is reached locally at `http://localhost:3000`; the Knowledge/Files
  APIs are under `/api/v1/...`.
- The ingestion tool lives in this repo at `tools/jarvis-ingest/`.
- Config-change tasks follow **apply → recreate → verify-with-expected-output**
  (not TDD — they are infrastructure). The ingestion tool follows **TDD**.
- Several Open WebUI env var names and API paths have bitten us before
  (`ENABLE_API_KEYS` was plural). **Task 0 verifies the exact names/paths against
  the running image before any task relies on them.**

---

## File / change map

| Path | Responsibility |
|---|---|
| `/opt/stacks/ai/docker-compose.yml` | stack config: vector DB, embedding engine (modified) |
| `/etc/systemd/system/ollama.service.d/override.conf` | restore ctx 4096 (modified) |
| Open WebUI DB (in `ai_open-webui` volume) | `jarvis-kb` collection, "Jarvis" model, Memory flag (runtime, via API/UI) |
| `tools/jarvis-ingest/jarvis_ingest/state.py` | content-hash state store (skip unchanged files) |
| `tools/jarvis-ingest/jarvis_ingest/adapters.py` | source adapters; v1 = local folder |
| `tools/jarvis-ingest/jarvis_ingest/client.py` | Open WebUI Knowledge API client |
| `tools/jarvis-ingest/jarvis_ingest/ingest.py` | orchestrator (dedupe + upload + error handling) |
| `tools/jarvis-ingest/jarvis_ingest/cli.py` | command-line entrypoint |
| `tools/jarvis-ingest/tests/*` | pytest unit tests |
| `tools/jarvis-ingest/requirements.txt`, `README.md` | deps + usage |

---

## Task 0: Verify env var names and API paths against the running image

**Files:** none (read-only investigation; record findings in the PR/commit notes).

- [ ] **Step 1: Confirm vector-DB + embedding env var names**

Run:
```bash
docker exec open-webui sh -c 'grep -nE "VECTOR_DB|QDRANT_URI|QDRANT_|RAG_EMBEDDING_ENGINE|RAG_EMBEDDING_MODEL|ENABLE_RAG" /app/backend/open_webui/config.py | head -40'
```
Expected: lines showing `VECTOR_DB`, `QDRANT_URI` (or `QDRANT_*`), `RAG_EMBEDDING_ENGINE`, `RAG_EMBEDDING_MODEL`. Note the exact spellings; if any differ from this plan, use the image's spelling in later tasks.

- [ ] **Step 2: Confirm Knowledge + Files API paths**

Run:
```bash
docker exec open-webui sh -c 'grep -rnE "@router\.(post|get)\(" /app/backend/open_webui/routers/knowledge.py /app/backend/open_webui/routers/files.py | head -40'
```
Expected: routes including a knowledge create (`/create`), a file-add to knowledge (e.g. `/{id}/file/add`), and a file upload in files router (e.g. `/`). Record the exact paths; later tasks assume:
- Create knowledge: `POST /api/v1/knowledge/create`
- Upload file: `POST /api/v1/files/`
- Attach file to knowledge: `POST /api/v1/knowledge/{id}/file/add`
- Create model: `POST /api/v1/models/create`
- Set model access: `POST /api/v1/models/model/access/update`

If the running image differs, update the constants in Task 6's `client.py` accordingly.

- [ ] **Step 3: Confirm an API key exists for the ingestion tool**

The owner account must mint an API key (Open WebUI → Settings → Account → API Keys). Export it for later tasks:
```bash
# done by the human operator; the key is NOT committed
export JARVIS_API_KEY=sk-...
export JARVIS_BASE_URL=http://localhost:3000
```
Expected: `curl -s -H "Authorization: Bearer $JARVIS_API_KEY" $JARVIS_BASE_URL/api/models | head -c 80` returns JSON (not 401).

---

## Task 1: Point Open WebUI at Qdrant (replace internal Chroma)

**Files:**
- Modify: `/opt/stacks/ai/docker-compose.yml` (open-webui `environment:` block)

- [ ] **Step 1: Add vector-DB env to the open-webui service**

Edit `/opt/stacks/ai/docker-compose.yml`, in the `open-webui` service `environment:` list, add (use exact names confirmed in Task 0):
```yaml
      - VECTOR_DB=qdrant
      - QDRANT_URI=http://qdrant:6333
```
(`qdrant` resolves over the shared compose network since both services share this compose project.)

- [ ] **Step 2: Recreate and wait for healthy**

Run:
```bash
cd /opt/stacks/ai && docker compose up -d
for i in $(seq 1 18); do s=$(docker inspect -f '{{.State.Health.Status}}' open-webui 2>/dev/null); [ "$s" = healthy ] && break; sleep 5; done; echo "health=$s"
```
Expected: `health=healthy`.

- [ ] **Step 3: Verify Open WebUI talks to Qdrant**

Run:
```bash
docker logs --since 2m open-webui 2>&1 | grep -iE "qdrant|vector" | tail -10
curl -s http://localhost:6333/collections | python3 -m json.tool
```
Expected: no Qdrant connection errors in logs; `/collections` returns a JSON result (an empty `collections` list is fine — it fills once documents are indexed).

- [ ] **Step 4: Archive the live compose into the repo and commit**

The live compose lives at `/opt/stacks/ai/` (outside the repo), so archive a copy:
```bash
cd /home/alienware/jarvis-box-setup
mkdir -p stacks/ai
cp /opt/stacks/ai/docker-compose.yml stacks/ai/docker-compose.yml
git add stacks/ai/docker-compose.yml
git commit -m "Archive ai compose: switch Open WebUI vector DB to Qdrant"
```
Expected: commit succeeds; `stacks/ai/docker-compose.yml` now tracked.

---

## Task 2: Configure CPU embeddings (built-in SentenceTransformers, bge-small)

**Files:**
- Modify: `/opt/stacks/ai/docker-compose.yml` (open-webui `environment:` block)

- [ ] **Step 1: Set the embedding model, leave engine at default**

In the same `environment:` list add (do NOT set `RAG_EMBEDDING_ENGINE=ollama` — that would push the embed model onto the GPU):
```yaml
      - RAG_EMBEDDING_ENGINE=
      - RAG_EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
```

- [ ] **Step 2: Recreate and wait for healthy**

Run:
```bash
cd /opt/stacks/ai && docker compose up -d
for i in $(seq 1 24); do s=$(docker inspect -f '{{.State.Health.Status}}' open-webui 2>/dev/null); [ "$s" = healthy ] && break; sleep 5; done; echo "health=$s"
```
Expected: `health=healthy` (first boot may take longer while the embedding model downloads).

- [ ] **Step 3: Verify the embedding model loaded on CPU**

Run:
```bash
docker logs --since 3m open-webui 2>&1 | grep -iE "embedding|bge|sentence" | tail -10
nvidia-smi --query-gpu=memory.used --format=csv,noheader
```
Expected: log lines referencing the bge embedding model; GPU `memory.used` unchanged from the hermes baseline (~4.9GB) — confirming embeddings did NOT land on the GPU.

- [ ] **Step 4: Archive + commit compose**

Run:
```bash
cp /opt/stacks/ai/docker-compose.yml /home/alienware/jarvis-box-setup/stacks/ai/docker-compose.yml
cd /home/alienware/jarvis-box-setup
git add stacks/ai/docker-compose.yml
git commit -m "Archive ai compose: CPU bge-small embeddings for RAG"
```
Expected: commit succeeds.

---

## Task 3: Restore Ollama context length to 4096

**Files:**
- Modify: `/etc/systemd/system/ollama.service.d/override.conf`

- [ ] **Step 1: Set context length back to 4096**

Run (shows the command before applying):
```bash
sudo sed -i 's/OLLAMA_CONTEXT_LENGTH=2048/OLLAMA_CONTEXT_LENGTH=4096/' /etc/systemd/system/ollama.service.d/override.conf
cat /etc/systemd/system/ollama.service.d/override.conf
```
Expected: drop-in shows `OLLAMA_CONTEXT_LENGTH=4096`, `OLLAMA_NUM_PARALLEL=2`.

- [ ] **Step 2: Reload + restart + warm**

Run:
```bash
sudo systemctl daemon-reload && sudo systemctl restart ollama && sleep 6
curl -s http://localhost:11434/api/generate -d '{"model":"hermes3:8b","prompt":"hi","stream":false}' -o /dev/null -w 'warm %{http_code}\n' --max-time 90
```
Expected: `warm 200`.

- [ ] **Step 3: Verify GPU placement — decide par=2 vs par=1**

Run:
```bash
ollama ps
```
Expected: `hermes3:8b` shows `100% GPU` and `CONTEXT 4096`.
**If it shows any `% CPU` (spill):** par=2 + ctx=4096 does not fit. Drop parallelism:
```bash
sudo sed -i 's/OLLAMA_NUM_PARALLEL=2/OLLAMA_NUM_PARALLEL=1/' /etc/systemd/system/ollama.service.d/override.conf
sudo systemctl daemon-reload && sudo systemctl restart ollama && sleep 6
curl -s http://localhost:11434/api/generate -d '{"model":"hermes3:8b","prompt":"hi","stream":false}' -o /dev/null -w '%{http_code}\n' --max-time 90
ollama ps
```
Expected after fallback: `100% GPU`, `CONTEXT 4096`, `NUM_PARALLEL=1`.

- [ ] **Step 4: Update the runbook**

Edit `/home/alienware/jarvis-box-setup/setup-plan.md` §5: change the documented `OLLAMA_CONTEXT_LENGTH` to 4096 and note the final `NUM_PARALLEL` value chosen in Step 3. Commit:
```bash
cd /home/alienware/jarvis-box-setup
git add setup-plan.md
git commit -m "Restore Ollama ctx 4096 for RAG (CPU embeddings free the GPU)"
```
Expected: commit succeeds.

---

## Task 4: Create the `jarvis-kb` knowledge collection

**Files:** none in repo (runtime object via API).

- [ ] **Step 1: Create the collection**

Run (uses the env from Task 0 Step 3):
```bash
curl -s -X POST "$JARVIS_BASE_URL/api/v1/knowledge/create" \
  -H "Authorization: Bearer $JARVIS_API_KEY" -H "Content-Type: application/json" \
  -d '{"name":"jarvis-kb","description":"Jarvis personal knowledge base"}' | tee /tmp/jarvis_kb.json | python3 -m json.tool
```
Expected: JSON with an `id` field. Save it:
```bash
JARVIS_KB_ID=$(python3 -c "import json;print(json.load(open('/tmp/jarvis_kb.json'))['id'])"); echo "$JARVIS_KB_ID"
```

- [ ] **Step 2: Verify it lists**

Run:
```bash
curl -s "$JARVIS_BASE_URL/api/v1/knowledge/" -H "Authorization: Bearer $JARVIS_API_KEY" | python3 -c "import sys,json;[print(k['id'],k['name']) for k in json.load(sys.stdin)]"
```
Expected: a line `<id> jarvis-kb`.

---

## Task 5: Create the public "Jarvis" workspace model (butler persona)

**Files:** none in repo (runtime object via API).

- [ ] **Step 1: Create the model with the butler system prompt and attached knowledge**

Run (substitutes `$JARVIS_KB_ID` from Task 4):
```bash
curl -s -X POST "$JARVIS_BASE_URL/api/v1/models/create" \
  -H "Authorization: Bearer $JARVIS_API_KEY" -H "Content-Type: application/json" \
  -d "$(python3 - <<PY
import json,os
print(json.dumps({
  "id":"jarvis",
  "name":"Jarvis",
  "base_model_id":"hermes3:8b",
  "meta":{
    "description":"Formal witty butler assistant with personal knowledge base.",
    "knowledge":[{"id":os.environ["JARVIS_KB_ID"]}]
  },
  "params":{
    "system":"You are JARVIS, a formal and witty personal butler-assistant in the style of a refined English butler. Address the user respectfully. Be concise, precise, and proactively helpful, with occasional dry wit. When you use information from the provided knowledge or memories, ground your answer in it and cite the source. If you do not know or the knowledge base contains nothing relevant, say so plainly rather than inventing facts."
  },
  "access_control":None
}))
PY
)" | python3 -m json.tool
```
Expected: JSON describing the created `jarvis` model.

- [ ] **Step 2: Make it public (so all accounts + API keys can use it)**

Run:
```bash
curl -s -X POST "$JARVIS_BASE_URL/api/v1/models/model/access/update" \
  -H "Authorization: Bearer $JARVIS_API_KEY" -H "Content-Type: application/json" \
  -d '{"id":"jarvis","name":"Jarvis","access_grants":[{"principal_type":"user","principal_id":"*","permission":"read"}]}' | python3 -m json.tool
```
Expected: JSON for `jarvis`; no error.

- [ ] **Step 3: Verify persona end-to-end**

Run:
```bash
curl -s "$JARVIS_BASE_URL/ollama/v1/chat/completions" \
  -H "Authorization: Bearer $JARVIS_API_KEY" -H "Content-Type: application/json" \
  -d '{"model":"jarvis","messages":[{"role":"user","content":"Introduce yourself in one sentence."}],"stream":false}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['choices'][0]['message']['content'])"
```
Expected: a single-sentence reply in a formal butler voice (addresses the user respectfully). If the model id `jarvis` is rejected, confirm via `GET /api/models` that `jarvis` is listed; the workspace model may need a moment to register.

---

## Task 6: Enable the per-user Memory feature

**Files:** none in repo (runtime setting).

- [ ] **Step 1: Verify the Memory toggle path in this image**

Run:
```bash
docker exec open-webui sh -c 'grep -rniE "enable_memory|/memories|personalization" /app/backend/open_webui/routers/memories.py 2>/dev/null | head; grep -rniE "ENABLE_MEMORY|MEMORY" /app/backend/open_webui/config.py | head'
```
Expected: confirms a memories router exists. In Open WebUI, Memory is enabled **per user** in the UI (Settings → Personalization → Memory → toggle on). There is no server env that force-enables it for everyone; document this for users.

- [ ] **Step 2: Functional check (manual, by the owner)**

In the UI as the owner, with the `Jarvis` model selected:
1. Settings → Personalization → Memory → enable.
2. In a chat: "Remember that I prefer metric units."
3. Start a NEW chat: "Which unit system do I prefer?"

Expected: the new chat answers "metric" without being re-told. Record the result in the PR notes (this is the spec's memory success criterion).

---

## Task 7: Scaffold the ingestion tool

**Files:**
- Create: `tools/jarvis-ingest/requirements.txt`
- Create: `tools/jarvis-ingest/jarvis_ingest/__init__.py`
- Create: `tools/jarvis-ingest/tests/__init__.py`
- Create: `tools/jarvis-ingest/pytest.ini`

- [ ] **Step 1: Create requirements and package skeleton**

`tools/jarvis-ingest/requirements.txt`:
```
requests==2.32.3
pytest==8.3.3
responses==0.25.3
```

`tools/jarvis-ingest/jarvis_ingest/__init__.py`:
```python
"""Jarvis ingestion tool: push files into the Open WebUI Knowledge API."""
```

`tools/jarvis-ingest/tests/__init__.py`:
```python
```

`tools/jarvis-ingest/pytest.ini`:
```ini
[pytest]
testpaths = tests
```

- [ ] **Step 2: Create a virtualenv and install**

Run:
```bash
cd /home/alienware/jarvis-box-setup/tools/jarvis-ingest
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
pytest -q
```
Expected: pip installs cleanly; `pytest -q` reports "no tests ran" (exit code 5 is fine at this stage).

- [ ] **Step 3: Commit**

```bash
cd /home/alienware/jarvis-box-setup
printf '%s\n' 'tools/jarvis-ingest/.venv/' 'tools/jarvis-ingest/.ingest-state.json' '__pycache__/' '.pytest_cache/' >> .gitignore
git add tools/jarvis-ingest/requirements.txt tools/jarvis-ingest/jarvis_ingest/__init__.py tools/jarvis-ingest/tests/__init__.py tools/jarvis-ingest/pytest.ini .gitignore
git commit -m "Scaffold jarvis-ingest tool"
```
Expected: commit succeeds.

---

## Task 8: State store (skip unchanged files) — TDD

**Files:**
- Create: `tools/jarvis-ingest/jarvis_ingest/state.py`
- Test: `tools/jarvis-ingest/tests/test_state.py`

- [ ] **Step 1: Write the failing test**

`tools/jarvis-ingest/tests/test_state.py`:
```python
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
    assert store.is_changed(f) is True          # never seen
    store.update(f)
    store.save()

    reloaded = StateStore(state_path)
    assert reloaded.is_changed(f) is False       # unchanged since recorded
    f.write_text("v2")
    assert reloaded.is_changed(f) is True         # content changed


def test_state_persisted_as_json(tmp_path):
    state_path = tmp_path / "state.json"
    f = tmp_path / "doc.txt"
    f.write_text("x")
    store = StateStore(state_path)
    store.update(f)
    store.save()
    data = json.loads(state_path.read_text())
    assert str(f.resolve()) in data
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
cd /home/alienware/jarvis-box-setup/tools/jarvis-ingest && . .venv/bin/activate && pytest tests/test_state.py -q
```
Expected: FAIL — `ModuleNotFoundError: No module named 'jarvis_ingest.state'`.

- [ ] **Step 3: Implement**

`tools/jarvis-ingest/jarvis_ingest/state.py`:
```python
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

    def is_changed(self, file_path: Path) -> bool:
        key = str(Path(file_path).resolve())
        return self._data.get(key) != file_hash(Path(file_path))

    def update(self, file_path: Path) -> None:
        key = str(Path(file_path).resolve())
        self._data[key] = file_hash(Path(file_path))

    def save(self) -> None:
        self.path.write_text(json.dumps(self._data, indent=2))
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
pytest tests/test_state.py -q
```
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
cd /home/alienware/jarvis-box-setup
git add tools/jarvis-ingest/jarvis_ingest/state.py tools/jarvis-ingest/tests/test_state.py
git commit -m "Add content-hash state store to jarvis-ingest"
```

---

## Task 9: Folder adapter — TDD

**Files:**
- Create: `tools/jarvis-ingest/jarvis_ingest/adapters.py`
- Test: `tools/jarvis-ingest/tests/test_adapters.py`

- [ ] **Step 1: Write the failing test**

`tools/jarvis-ingest/tests/test_adapters.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
pytest tests/test_adapters.py -q
```
Expected: FAIL — `ModuleNotFoundError: No module named 'jarvis_ingest.adapters'`.

- [ ] **Step 3: Implement**

`tools/jarvis-ingest/jarvis_ingest/adapters.py`:
```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
pytest tests/test_adapters.py -q
```
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
cd /home/alienware/jarvis-box-setup
git add tools/jarvis-ingest/jarvis_ingest/adapters.py tools/jarvis-ingest/tests/test_adapters.py
git commit -m "Add folder adapter to jarvis-ingest"
```

---

## Task 10: Open WebUI Knowledge API client — TDD

**Files:**
- Create: `tools/jarvis-ingest/jarvis_ingest/client.py`
- Test: `tools/jarvis-ingest/tests/test_client.py`

- [ ] **Step 1: Write the failing test**

`tools/jarvis-ingest/tests/test_client.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
pytest tests/test_client.py -q
```
Expected: FAIL — `ModuleNotFoundError: No module named 'jarvis_ingest.client'`.

- [ ] **Step 3: Implement**

`tools/jarvis-ingest/jarvis_ingest/client.py`:
```python
from pathlib import Path
import requests


class OpenWebUIClient:
    """Minimal client for the Open WebUI Files + Knowledge APIs.

    Endpoint paths confirmed in Task 0; change here if the image differs.
    """

    def __init__(self, base_url: str, api_key: str, timeout: int = 60):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {api_key}"

    def upload_file(self, path: Path) -> str:
        with open(path, "rb") as fh:
            resp = self.session.post(
                f"{self.base_url}/api/v1/files/",
                files={"file": (Path(path).name, fh)},
                timeout=self.timeout,
            )
        resp.raise_for_status()
        return resp.json()["id"]

    def attach_file_to_knowledge(self, knowledge_id: str, file_id: str) -> None:
        resp = self.session.post(
            f"{self.base_url}/api/v1/knowledge/{knowledge_id}/file/add",
            json={"file_id": file_id},
            timeout=self.timeout,
        )
        resp.raise_for_status()
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
pytest tests/test_client.py -q
```
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
cd /home/alienware/jarvis-box-setup
git add tools/jarvis-ingest/jarvis_ingest/client.py tools/jarvis-ingest/tests/test_client.py
git commit -m "Add Open WebUI knowledge client to jarvis-ingest"
```

---

## Task 11: Orchestrator (dedupe + upload + skip-bad + retry) — TDD

**Files:**
- Create: `tools/jarvis-ingest/jarvis_ingest/ingest.py`
- Test: `tools/jarvis-ingest/tests/test_ingest.py`

- [ ] **Step 1: Write the failing test**

`tools/jarvis-ingest/tests/test_ingest.py`:
```python
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
    assert state.is_changed(tmp_path / "bad.txt") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
pytest tests/test_ingest.py -q
```
Expected: FAIL — `ModuleNotFoundError: No module named 'jarvis_ingest.ingest'`.

- [ ] **Step 3: Implement**

`tools/jarvis-ingest/jarvis_ingest/ingest.py`:
```python
import logging
import time

log = logging.getLogger("jarvis_ingest")


def ingest(adapter, client, state, knowledge_id, retries: int = 2):
    """Upload changed files into the knowledge base.

    Returns a summary dict: uploaded / skipped / failed counts.
    Failed files are NOT recorded in state, so they retry on the next run.
    """
    uploaded = skipped = failed = 0
    for path in adapter.iter_files():
        if not state.is_changed(path):
            skipped += 1
            continue
        try:
            file_id = _with_retries(lambda: client.upload_file(path), retries)
            _with_retries(
                lambda: client.attach_file_to_knowledge(knowledge_id, file_id),
                retries,
            )
            state.update(path)
            uploaded += 1
        except Exception as exc:  # noqa: BLE001 - log and continue, never crash batch
            log.warning("skip %s: %s", path, exc)
            failed += 1
    state.save()
    return {"uploaded": uploaded, "skipped": skipped, "failed": failed}


def _with_retries(fn, retries: int):
    last = None
    for attempt in range(retries + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < retries:
                time.sleep(0.1 * (attempt + 1))
    raise last
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
pytest tests/test_ingest.py -q
```
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
cd /home/alienware/jarvis-box-setup
git add tools/jarvis-ingest/jarvis_ingest/ingest.py tools/jarvis-ingest/tests/test_ingest.py
git commit -m "Add ingestion orchestrator with dedupe and retry"
```

---

## Task 12: CLI entrypoint

**Files:**
- Create: `tools/jarvis-ingest/jarvis_ingest/cli.py`
- Test: `tools/jarvis-ingest/tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

`tools/jarvis-ingest/tests/test_cli.py`:
```python
from jarvis_ingest.cli import build_parser


def test_parser_requires_source_and_kb():
    parser = build_parser()
    args = parser.parse_args(
        ["--source", "/data/docs", "--knowledge-id", "kb-1",
         "--base-url", "http://x", "--api-key", "sk", "--state", "/tmp/s.json"]
    )
    assert args.source == "/data/docs"
    assert args.knowledge_id == "kb-1"
    assert args.base_url == "http://x"
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
pytest tests/test_cli.py -q
```
Expected: FAIL — `ModuleNotFoundError: No module named 'jarvis_ingest.cli'`.

- [ ] **Step 3: Implement**

`tools/jarvis-ingest/jarvis_ingest/cli.py`:
```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
pytest tests/test_cli.py -q && pytest -q
```
Expected: `test_cli.py` PASS; full suite PASS (all tests green).

- [ ] **Step 5: Commit**

```bash
cd /home/alienware/jarvis-box-setup
git add tools/jarvis-ingest/jarvis_ingest/cli.py tools/jarvis-ingest/tests/test_cli.py
git commit -m "Add jarvis-ingest CLI entrypoint"
```

---

## Task 13: End-to-end RAG verification + README

**Files:**
- Create: `tools/jarvis-ingest/README.md`

- [ ] **Step 1: Ingest a known test document**

Run (env from Task 0, `$JARVIS_KB_ID` from Task 4):
```bash
cd /home/alienware/jarvis-box-setup/tools/jarvis-ingest && . .venv/bin/activate
mkdir -p /tmp/jarvis-docs
printf 'The Jarvis box internal codename for the GPU is "Tin Man".\n' > /tmp/jarvis-docs/codename.txt
python -m jarvis_ingest.cli --source /tmp/jarvis-docs --knowledge-id "$JARVIS_KB_ID" --state /tmp/jarvis-ingest-state.json
```
Expected: log line `done: {'uploaded': 1, 'skipped': 0, 'failed': 0}`.

- [ ] **Step 2: Confirm the vector landed in Qdrant**

Run:
```bash
curl -s http://localhost:6333/collections | python3 -m json.tool
```
Expected: at least one collection present (Open WebUI created it during embedding).

- [ ] **Step 3: Ask Jarvis a question only answerable from the document**

Run:
```bash
curl -s "$JARVIS_BASE_URL/ollama/v1/chat/completions" \
  -H "Authorization: Bearer $JARVIS_API_KEY" -H "Content-Type: application/json" \
  -d '{"model":"jarvis","messages":[{"role":"user","content":"What is the internal codename for the GPU?"}],"stream":false}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['choices'][0]['message']['content'])"
```
Expected: an answer containing "Tin Man" (grounded in the ingested doc). If RAG context is not being pulled via the API path, verify in the UI by selecting the `Jarvis` model and asking the same question — the workspace model's attached knowledge applies in the chat UI.

- [ ] **Step 4: Re-run ingestion to confirm dedupe**

Run:
```bash
python -m jarvis_ingest.cli --source /tmp/jarvis-docs --knowledge-id "$JARVIS_KB_ID" --state /tmp/jarvis-ingest-state.json
```
Expected: `done: {'uploaded': 0, 'skipped': 1, 'failed': 0}`.

- [ ] **Step 5: Write the README and commit**

`tools/jarvis-ingest/README.md`:
```markdown
# jarvis-ingest

Pushes files into the Open WebUI Knowledge base that backs the "Jarvis" model.

## Setup
    python3 -m venv .venv && . .venv/bin/activate
    pip install -r requirements.txt

## Usage
    export JARVIS_BASE_URL=http://localhost:3000
    export JARVIS_API_KEY=sk-...           # Open WebUI -> Settings -> Account -> API Keys
    python -m jarvis_ingest.cli \
      --source /path/to/docs \
      --knowledge-id <jarvis-kb id>        # from POST /api/v1/knowledge/create

Re-runs skip unchanged files (tracked by content hash in the --state file).
Unreadable/failed files are logged and retried on the next run.

## Tests
    pytest -q

## Extending sources
Add a new adapter class in `jarvis_ingest/adapters.py` exposing `iter_files()`;
the orchestrator and CLI consume any adapter with that interface.
```

Run:
```bash
cd /home/alienware/jarvis-box-setup
git add tools/jarvis-ingest/README.md
git commit -m "Add jarvis-ingest README and document end-to-end RAG verification"
```
Expected: commit succeeds.

---

## Final verification checklist (run all, report each)

1. `ollama ps` → `hermes3:8b` 100% GPU, CONTEXT 4096.
2. `curl -s localhost:6333/collections` → returns JSON (≥1 collection after ingest).
3. `GET /api/models` (with API key) → lists `jarvis`.
4. Persona check (Task 5 Step 3) → butler-voice reply.
5. Memory check (Task 6 Step 2) → recalls a fact in a new chat.
6. RAG check (Task 13 Step 3) → answer contains "Tin Man".
7. Dedupe check (Task 13 Step 4) → second run uploads 0.
8. `pytest -q` in `tools/jarvis-ingest` → all green.
9. (Optional scale smoke test — spec criterion #5) Ingest a few hundred real
   files: `python -m jarvis_ingest.cli --source <big-folder> --knowledge-id "$JARVIS_KB_ID" --state /tmp/jarvis-ingest-state.json`.
   Then time a query; confirm retrieval feels sub-second and the answer is
   coherent. Watch `nvidia-smi` during indexing to confirm embeddings stay off
   the GPU (used MiB ≈ hermes baseline).
```
