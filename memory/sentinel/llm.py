import json
import os
import urllib.request

DEFAULT_MODEL = "qwen3:8b"


def provider() -> str:
    return (os.environ.get("LLM_PROVIDER") or "ollama").strip().lower()


def model() -> str:
    return os.environ.get("LLM_MODEL") or DEFAULT_MODEL


def describe() -> str:
    return f"{provider()}:{model()}"


def generate(prompt: str, system: str, temperature: float = 0.0, timeout: int = 180) -> str:
    if provider() == "ollama":
        return _ollama(prompt, system, temperature, timeout)
    return _openai_compatible(prompt, system, temperature, timeout)


def _endpoint(default: str) -> str:
    return (os.environ.get("LLM_ENDPOINT") or default).rstrip("/")


def _post(url: str, body: dict, headers: dict[str, str], timeout: int) -> dict:
    request = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def _ollama(prompt: str, system: str, temperature: float, timeout: int) -> str:
    base = _endpoint("http://localhost:11434")
    for suffix in ("/v1", "/api/generate", "/api"):
        base = base.removesuffix(suffix)
    payload = _post(f"{base}/api/generate", {
        "model": model(), "system": system, "prompt": prompt, "stream": False, "think": False,
        "options": {"temperature": temperature, "num_ctx": int(os.environ.get("LLM_NUM_CTX") or 16384)},
    }, {}, timeout)
    return (payload.get("response") or "").strip()


def _openai_compatible(prompt: str, system: str, temperature: float, timeout: int) -> str:
    base = _endpoint("https://api.openai.com/v1")
    key = os.environ.get("LLM_API_KEY") or ""
    name = model().split("/", 1)[-1]
    payload = _post(f"{base}/chat/completions", {
        "model": name, "temperature": temperature,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
    }, {"Authorization": f"Bearer {key}"} if key else {}, timeout)
    choices = payload.get("choices") or []
    return (choices[0].get("message", {}).get("content") or "").strip() if choices else ""
