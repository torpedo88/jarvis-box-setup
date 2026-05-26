# Conversational Jarvis — Design Spec (Slice A)

Date: 2026-05-26
Status: Approved design, pre-implementation
Repo: jarvis-box-setup

## Context

The Jarvis box runs Open WebUI + Ollama (`hermes3:8b`, pinned) + Qdrant, exposed
publicly (login-gated) via Tailscale Funnel. The larger goal is an agentic
personal assistant across devices. That whole vision is a platform of
subsystems; this spec covers only **Slice A: Conversational Jarvis** — the
foundation everything else builds on.

Slice A is explicitly NOT agentic task execution (that is Slice B, deferred,
and likely needs a more capable "brain" than a local 8B model).

### Hardware reality (governs every choice)

| Resource | Available |
|---|---|
| GPU | RTX 2060, 6GB VRAM — `hermes3:8b` pins ~4.9GB |
| RAM | 30GB (≈27GB free) |
| CPU | 12 cores |
| Disk | 62GB free |

The GPU is the only scarce resource. RAM/CPU abundance is the lever we pull to
keep the GPU free for text generation.

## Goals (success criteria)

1. **Persona:** Jarvis answers in a consistent formal-witty-butler voice
   (Iron Man "JARVIS" register; addresses the user respectfully, dry wit).
2. **Memory:** State a fact in one chat → it is recalled in a later, separate
   chat without re-stating.
3. **RAG:** Load a document → ask a question only answerable from it → get a
   grounded answer that cites the source.
4. **Drafting:** Usable as a thinking partner — brainstorming, drafting,
   summarizing — in the butler voice.
5. **Scale smoke test:** Index a few hundred documents → query latency remains
   acceptable (target: retrieval < ~1s; total response dominated by generation).

## Non-goals (YAGNI / deferred)

- Agentic tool/function calling and task execution (Slice B).
- Integrations: calendar, email, messaging, smart home (Slice C).
- Voice / wake word (Slice D).
- Deep device automation, iOS Shortcuts / Android Tasker (Slice E).
- Query-time reranking (optional later, CPU-only).
- Multiple ingestion source adapters beyond local folders (designed for, not
  built in v1).

## Architecture (Approach 2: Open WebUI-native RAG + thin ingestion script)

Four units, each independently understandable and testable:

### Unit 1 — Persona: Open WebUI workspace model "Jarvis"

A workspace **model object** (stored in the Open WebUI DB, which lives in the
`ai_open-webui` volume), NOT an Ollama Modelfile.

- Base model: `hermes3:8b`.
- System prompt: butler persona (formal, witty, respectful address, concise,
  proactive; admits uncertainty rather than inventing).
- Attached knowledge collection: `jarvis-kb` (see Unit 3).
- Access: **public** (`user:* read` grant) so it appears for all accounts and
  works via the API, consistent with the existing multi-user setup.

Rationale: a workspace model can bind BOTH the system prompt AND a knowledge
collection in one object; an Ollama Modelfile cannot attach Open WebUI
Knowledge. This keeps persona + RAG as a single configurable artifact.

### Unit 2 — Memory

Open WebUI's built-in Memory feature (per-user, Settings → Personalization →
Memory). Enabled via configuration. Facts are injected into context on recall.
Zero VRAM cost. Per-user by design (each account's memories are private).

### Unit 3 — RAG pipeline (embeddings on CPU)

- **Vector store:** point Open WebUI at the existing Qdrant container instead of
  its internal Chroma. Compose env: `VECTOR_DB=qdrant` and the Qdrant URI
  (e.g. `QDRANT_URI=http://qdrant:6333`, on the shared compose network).
- **Embeddings (CPU):** use Open WebUI's **built-in** embedding engine
  (`RAG_EMBEDDING_ENGINE` left at default / empty — the SentenceTransformers
  path), NOT the Ollama engine. The `open-webui` container has no GPU access in
  this compose, so its built-in embeddings run on **CPU** by construction —
  exactly what we want. Do NOT use `RAG_EMBEDDING_ENGINE=ollama`: that would run
  the embed model inside Ollama, which places it on the **GPU** and competes
  with the pinned `hermes3:8b`.
  - Embedding model: a small, quality SentenceTransformers model, e.g.
    `BAAI/bge-small-en-v1.5` (good retrieval quality, light on CPU). The stock
    default (`all-MiniLM-L6-v2`) is an acceptable fallback.
  - 12 cores make CPU embedding viable for ongoing trickle ingestion; the
    initial bulk index is a slower one-time cost (batch/run overnight if huge).
- **GPU dividend:** with the GPU no longer needed for embeddings, restore
  `OLLAMA_CONTEXT_LENGTH=4096` (revert the 2048 RAG-squeeze) for better
  retrieved-context capacity. Keep `OLLAMA_NUM_PARALLEL=2` only if
  `ollama ps` still shows 100% GPU at ctx 4096 with par 2; otherwise drop to
  par 1. (Verify empirically — KV-cache = par × ctx.)
- **Knowledge collection:** `jarvis-kb`, attached to the Jarvis model.
- **Chunking / top-k:** Open WebUI RAG defaults initially; tune after the
  scale smoke test.
- **Reranking:** skipped in v1.

### Unit 4 — Ingestion script

A standalone Python tool with an **adapter interface**; v1 ships exactly one
adapter (local folder tree).

- Walks the configured source, computes a content hash per file, and maintains
  a small local state file (path → hash) to skip unchanged files (idempotent).
- For new/changed files: POST to the Open WebUI Knowledge API to add the file
  to `jarvis-kb` (which triggers embedding into Qdrant). Authenticates with an
  API key.
- Robustness: unreadable/unsupported files are logged and skipped — never crash
  the batch. Transient HTTP errors are retried with backoff.
- Invocation: on-demand (CLI) now; a systemd timer can schedule it later.
- Extensibility: notes-export and web adapters slot in behind the same
  interface without touching the upload/dedupe core.

## Data flow

**Ingest:**
`source files → ingestion script (hash/dedupe) → Open WebUI Knowledge API
→ built-in SentenceTransformers embedding (CPU) → chunks stored in Qdrant`

**Query:**
`user message → Open WebUI retrieves top-k from Qdrant (CPU/RAM) → injects
retrieved chunks + recalled memories into context → hermes3:8b (butler persona)
generates a cited answer`

## VRAM / resource budget

- `hermes3:8b` pinned ~4.9GB (generation only).
- Embeddings run on CPU → 0 GPU.
- Query-time vector search is CPU/RAM (Qdrant).
- Net effect: the GPU does only generation, so context length can return to
  4096. No concurrent on-GPU model contention.

## Error handling

- **Ingestion:** skip + log bad files; retry transient API errors with backoff;
  dedupe by hash so re-runs are safe.
- **Embedding load issues:** CPU path avoids GPU OOM entirely; if CPU indexing
  is too slow for a huge initial batch, chunk the batch / run overnight.
- **Empty retrieval:** Jarvis answers from general knowledge and states that it
  found nothing in the knowledge base (no fabricated citations).
- **Qdrant unreachable:** surface a clear error; chat still works without RAG.

## Testing

- **Persona:** prompt several varied messages; confirm consistent butler tone.
- **Memory:** state a fact → new chat → confirm recall.
- **RAG:** add a document containing a unique fact → ask for that fact →
  confirm grounded, cited answer; ask something absent → confirm honest "not
  found".
- **Ingestion script (unit):** run against a mocked Knowledge API — verify
  dedupe (unchanged files skipped), bad-file skip, retry on transient error.
- **Scale smoke test:** index a few hundred real files → measure retrieval
  latency and answer quality.

## Open decisions deferred to implementation

- Exact Open WebUI env var names/values for Qdrant + embedding engine (verify
  against the running image's config, as names have bitten us before, e.g.
  `ENABLE_API_KEYS`).
- Whether par=2 holds at ctx=4096 (empirical `ollama ps` check).
- Ingestion state-file format and Knowledge API endpoint path (confirm from the
  running image).
