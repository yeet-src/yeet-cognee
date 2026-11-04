"""Client for the yeet sysgraph service -- the ground-truth source.

The yeet side runs as a service on the observed host and exposes one HTTP
route that returns a complete host reading as JSON, assembled live from the
system graph (see ../yeet-service). This client just fetches that reading.

It replaces the old shell-out to `yeet graph query` over SSH: the yeet side
now owns the queries and the assembly, and the cognee side consumes an API.

    SENTINEL_READING_URL=http://host:9490/reading   (default http://127.0.0.1:9490/reading)
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request


class YeetError(RuntimeError):
    pass


DEFAULT_URL = "http://127.0.0.1:9490/reading"


class Yeet:
    """Fetches assembled host readings from the sysgraph service."""

    def __init__(self, url: str | None = None, timeout: int = 30):
        self.url = url or os.environ.get("SENTINEL_READING_URL") or DEFAULT_URL
        self.timeout = timeout

    def reading(self, watch: list[str], self_markers: list[str] | None = None,
                top_other: int = 5) -> dict:
        """One complete host reading: whole-box state, watched workloads in
        full (with sockets attributed), and the largest background processes."""
        params = {"top": str(top_other)}
        if watch:
            params["watch"] = ",".join(watch)
        if self_markers:
            params["self"] = ",".join(self_markers)
        url = f"{self.url}?{urllib.parse.urlencode(params)}"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise YeetError(
                f"could not reach the sysgraph service at {self.url}: {exc.reason}. "
                f"Is it running? `yeet service tree` should list `sentinel-sysgraph`."
            ) from exc
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise YeetError(f"non-JSON response from {url}: {body[:400]}") from exc
        if isinstance(payload, dict) and payload.get("error"):
            raise YeetError(str(payload["error"]))
        return payload
