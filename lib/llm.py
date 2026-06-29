"""Single OpenAI entry point for every LLM call in the live app.

OpenAI is the only LLM provider in this codebase. Set OPENAI_API_KEY (and
optionally OPENAI_MODEL)
to enable. Every caller degrades gracefully to its own template/fallback when the
key is missing or the request fails, so a dead key never breaks a send, an
enrichment, or a reply poll.
"""
import json
import os
import re

import requests

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_URL = "https://api.openai.com/v1/chat/completions"


def enabled() -> bool:
    return bool(OPENAI_API_KEY)


def _extract_json(text: str | None) -> dict | None:
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        d = json.loads(m.group(0) if m else text)
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def chat_json(system: str, user: str, *, max_tokens: int = 900,
              model: str | None = None, tag: bool = True) -> dict | None:
    """Return the parsed JSON object from an OpenAI chat completion (JSON mode),
    or None on any failure. With tag=True the chosen model is recorded under the
    '_model' key so callers can surface which engine wrote a draft."""
    if not OPENAI_API_KEY:
        return None
    mdl = model or OPENAI_MODEL
    try:
        r = requests.post(
            OPENAI_URL,
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": mdl,
                "max_tokens": max_tokens,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            timeout=60,
        )
        if r.status_code != 200:
            return None
        text = (((r.json() or {}).get("choices") or [{}])[0].get("message") or {}).get("content", "")
        d = _extract_json(text)
        if d is not None and tag:
            d["_model"] = f"openai:{mdl}"
        return d
    except Exception:
        return None
