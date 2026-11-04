"""A direct line to the local Ollama model.

The sentinel uses cognee to *retrieve* memory, but does the final reasoning
with a tightly controlled prompt of its own. Keeping that last step out of
cognee's generic question-answering is what lets us enforce the one rule the
demo lives or dies by: the live reading is ground truth, and the model must
not claim a path or endpoint was seen unless it is actually in that reading.
"""

from __future__ import annotations

import json
import os
import urllib.request


def _endpoint() -> str:
    # LLM_ENDPOINT in .env points cognee at Ollama; reuse it, stripping any
    # OpenAI-style suffix so we hit the native /api/generate route.
    base = os.environ.get("LLM_ENDPOINT", "http://localhost:11434")
    base = base.rstrip("/")
    for suffix in ("/v1", "/api/generate", "/api"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
    return base.rstrip("/")


def generate(prompt: str, system: str, model: str | None = None,
             temperature: float = 0.0, timeout: int = 180) -> str:
    """Single-shot completion from the local model. Deterministic by default."""
    model = model or os.environ.get("LLM_MODEL", "qwen3:8b")
    url = f"{_endpoint()}/api/generate"
    body = json.dumps({
        "model": model,
        "system": system,
        "prompt": prompt,
        "stream": False,
        "think": False,          # qwen3 is a hybrid-reasoning model; skip the
        "options": {"temperature": temperature},  # visible chain-of-thought
    }).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read())
    return (payload.get("response") or "").strip()
