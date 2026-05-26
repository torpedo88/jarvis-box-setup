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
