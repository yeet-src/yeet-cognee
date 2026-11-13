import json
import os
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = "http://127.0.0.1:9490"


class ReaderError(RuntimeError):
    pass


class Reader:
    def __init__(self, url: str | None = None, timeout: int = 30):
        self.url = (url or os.environ.get("SENTINEL_READER_URL") or DEFAULT_URL).rstrip("/")
        self.timeout = timeout

    def activity(self, watch: list[str], since_ms: int, until_ms: int | None = None, limit: int = 4000) -> dict:
        params = {"watch": ",".join(watch), "since": str(since_ms), "limit": str(limit)}
        if until_ms is not None:
            params["until"] = str(until_ms)
        return self._get(f"{self.url}/activity?{urllib.parse.urlencode(params)}")

    def _get(self, url: str) -> dict:
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as response:
                payload = json.loads(response.read())
        except urllib.error.URLError as exc:
            raise ReaderError(f"no yeet reader at {self.url}: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise ReaderError(f"non-JSON response from {url}") from exc
        if payload.get("error"):
            raise ReaderError(payload["error"])
        return payload
