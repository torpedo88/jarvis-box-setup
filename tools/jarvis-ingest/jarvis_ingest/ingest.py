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
