"""Bro gateway client (stdlib urllib only).

Talks to the local Bro endpoint (AI Horde-backed uncensored chain).
Never raises and never fails hard: any failure returns None / False so
agents can fall back to running without an LLM.
"""

import json
import urllib.error
import urllib.request

# Module-level constants so tests can monkeypatch the URL to a dead port.
BRO_BASE_URL = "http://127.0.0.1:8898"
BRO_URL = BRO_BASE_URL + "/v1/chat/completions"
BRO_HEALTH_URL = BRO_BASE_URL + "/health"


def _health_url():
    """Derive the health URL from BRO_URL so a monkeypatched BRO_URL is
    honoured by bro_available() too."""
    try:
        base = BRO_URL.split("/v1/")[0]
        if base:
            return base + "/health"
    except Exception:
        pass
    return BRO_HEALTH_URL


def call_bro(prompt, system=""):
    """Send ``prompt`` to the Bro gateway. Returns the reply text, or None
    on any failure (gateway down, non-200, bad JSON, empty choices)."""
    if prompt is None:
        return None
    payload = {
        "model": "bro",
        "messages": [
            {"role": "system", "content": system or ""},
            {"role": "user", "content": str(prompt)},
        ],
        "temperature": 0.7,
        "max_tokens": 800,
    }
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            BRO_URL, data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            if resp.status != 200:
                return None
            raw = resp.read()
        parsed = json.loads(raw.decode("utf-8"))
        choices = parsed.get("choices") if isinstance(parsed, dict) else None
        if not choices:
            return None
        message = choices[0].get("message") or {}
        content = message.get("content")
        if not content or not str(content).strip():
            return None
        return str(content)
    except Exception:
        return None


def bro_available():
    """True when the Bro gateway answers GET /health, else False."""
    try:
        req = urllib.request.Request(_health_url(), method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False
