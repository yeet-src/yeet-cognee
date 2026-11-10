"""The verdict model: whichever LLM cognee is configured to use.

The sentinel uses cognee to *retrieve* memory, but does the final reasoning
with a tightly controlled prompt of its own. Keeping that last step out of
cognee's generic question-answering is what lets us enforce the one rule the
demo lives or dies by: the live reading is ground truth, and the model must
not claim a path or endpoint was seen unless it is actually in that reading.

There is no second model to set up: this reads the same LLM_* variables
cognee reads. `LLM_PROVIDER=ollama` talks to Ollama's native API; anything
else is treated as an OpenAI-compatible chat endpoint (OpenAI itself, or a
gateway that speaks its protocol), with LLM_API_KEY as the bearer token.
"""

from __future__ import annotations

import json
import os
import urllib.request


def _provider() -> str:
    return (os.environ.get("LLM_PROVIDER") or "ollama").strip().lower()


def _endpoint(default: str) -> str:
    return (os.environ.get("LLM_ENDPOINT") or default).rstrip("/")


def _post(url: str, body: dict, headers: dict[str, str], timeout: int) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _ollama(prompt: str, system: str, model: str, temperature: float, timeout: int) -> str:
    base = _endpoint("http://localhost:11434")
    for suffix in ("/v1", "/api/generate", "/api"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
    payload = _post(f"{base}/api/generate", {
        "model": model, "system": system, "prompt": prompt, "stream": False,
        "think": False,  # qwen3 is a hybrid-reasoning model; skip the visible chain-of-thought
        # Ollama's default context is 4096 tokens; a fleet's reading plus the
        # recalled memory is more than that, and a truncated prompt loses the
        # question. Overridable for small models with LLM_NUM_CTX.
        "options": {"temperature": temperature, "num_ctx": int(os.environ.get("LLM_NUM_CTX") or 16384)},
    }, {}, timeout)
    return (payload.get("response") or "").strip()


def _openai_compatible(prompt: str, system: str, model: str, temperature: float, timeout: int) -> str:
    base = _endpoint("https://api.openai.com/v1")
    if not base.endswith("/v1") and "openai.com" in base:
        base += "/v1"
    key = os.environ.get("LLM_API_KEY") or ""
    # Some providers strip a vendor prefix cognee's config carries ("openai/gpt-4o-mini").
    model = model.split("/", 1)[1] if "/" in model and not model.startswith(("gpt-", "o1", "o3", "o4")) else model
    payload = _post(f"{base}/chat/completions", {
        "model": model, "temperature": temperature,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
    }, {"Authorization": f"Bearer {key}"} if key else {}, timeout)
    choices = payload.get("choices") or []
    return (choices[0].get("message", {}).get("content") or "").strip() if choices else ""


def generate(prompt: str, system: str, model: str | None = None,
             temperature: float = 0.0, timeout: int = 180) -> str:
    """Single-shot completion from the configured model. Deterministic by default."""
    model = model or os.environ.get("LLM_MODEL") or "qwen3:8b"
    if _provider() == "ollama":
        return _ollama(prompt, system, model, temperature, timeout)
    return _openai_compatible(prompt, system, model, temperature, timeout)


def describe() -> str:
    return f"{_provider()}:{os.environ.get('LLM_MODEL') or 'qwen3:8b'}"
