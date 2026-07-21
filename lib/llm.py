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
# F14: bound each tool-loop completion so an unbounded response can't run up cost.
TOOL_MAX_TOKENS = int(os.environ.get("LEADGEN_TOOL_MAX_TOKENS", "1500"))
# F24: only allow known models; an unrecognised override falls back to the default.
ALLOWED_MODELS = {m.strip() for m in os.environ.get(
    "LEADGEN_ALLOWED_MODELS", "gpt-4o-mini,gpt-4o,gpt-4.1-mini,gpt-4.1,gpt-5,gpt-5-mini"
).split(",") if m.strip()}


def _safe_model(model: str | None) -> str:
    m = model or OPENAI_MODEL
    if ALLOWED_MODELS and m not in ALLOWED_MODELS:
        # B13: a silently-swapped model is a foot-gun — surface it (rate-limited).
        try:
            from lib import ops
            ops.alert("llm-model", f"requested model '{m}' not in LEADGEN_ALLOWED_MODELS; using '{OPENAI_MODEL}'")
        except Exception:
            pass
        return OPENAI_MODEL
    return m


def _token_field(model: str) -> str:
    """B13: newer models (gpt-5*, o-series) reject max_tokens and require
    max_completion_tokens; older chat models still take max_tokens."""
    return ("max_completion_tokens"
            if model.startswith(("gpt-5", "o1", "o3", "o4")) else "max_tokens")


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
    mdl = _safe_model(model)
    try:
        r = requests.post(
            OPENAI_URL,
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": mdl,
                _token_field(mdl): max_tokens,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            timeout=60,
        )
        if r.status_code != 200:
            _log_llm_failure(f"openai {r.status_code}", r.text[:300])
            return None
        text = (((r.json() or {}).get("choices") or [{}])[0].get("message") or {}).get("content", "")
        d = _extract_json(text)
        if d is not None and tag:
            d["_model"] = f"openai:{mdl}"
        return d
    except Exception:
        return None


def _log_llm_failure(what: str, detail: str = "") -> None:
    """2.2: an LLM failure must be visible in system_events, not silent (C6)."""
    try:
        from lib import ops
        ops.log_event("error", "llm", what, {"detail": detail[:300]})
    except Exception:
        print(f"[llm] {what}: {detail[:200]}")


def _post_chat(payload: dict) -> dict | None:
    """Raw chat-completions POST using the same client config as chat_json."""
    try:
        r = requests.post(
            OPENAI_URL,
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=120,
        )
        if r.status_code != 200:
            _log_llm_failure(f"openai {r.status_code}", r.text[:300])
            return None
        return r.json()
    except Exception as e:
        _log_llm_failure("openai request exception", str(e))
        return None


def chat_tools(messages, tools, tool_impls, emit, max_rounds=8, model=None):
    """Run an OpenAI function-calling loop. Returns the final assistant text.

    messages   : list of OpenAI-format message dicts; mutated in place as the loop runs.
    tools      : list of OpenAI tool schemas.
    tool_impls : dict name -> callable(args: dict, emit) -> JSON-serializable result.
    emit       : callable(event: dict) streaming tool_call/tool_result/assistant events.
    max_rounds : hard cap on tool-call rounds.
    model      : optional model override (defaults to OPENAI_MODEL, same as chat_json).
    """
    if not OPENAI_API_KEY:
        return ""
    mdl = _safe_model(model)

    def _msg(data):
        return (((data or {}).get("choices") or [{}])[0].get("message") or {})

    for _ in range(max_rounds):
        data = _post_chat({"model": mdl, "messages": messages,
                           "tools": tools, "tool_choice": "auto",
                           "max_tokens": TOOL_MAX_TOKENS})
        msg = _msg(data)
        calls = msg.get("tool_calls") or []
        if not calls:
            text = msg.get("content") or ""
            emit({"type": "assistant", "text": text})
            return text

        # Assistant turn requested tools; record it, then run each call.
        messages.append({"role": "assistant", "content": msg.get("content"),
                         "tool_calls": calls})
        for call in calls:
            fn = (call.get("function") or {})
            name = fn.get("name") or ""
            call_id = call.get("id")
            try:
                args = json.loads(fn.get("arguments") or "{}")
                if not isinstance(args, dict):
                    args = {}
            except Exception as e:
                result = {"error": f"bad arguments JSON: {e}"}
                emit({"type": "tool_call", "name": name, "args": {}})
            else:
                emit({"type": "tool_call", "name": name, "args": args})
                impl = tool_impls.get(name)
                if impl is None:
                    result = {"error": f"unknown tool: {name}"}
                else:
                    try:
                        result = impl(args, emit)
                    except Exception as e:
                        result = {"error": str(e)}
            content = json.dumps(result)
            emit({"type": "tool_result", "name": name, "summary": content[:200]})
            messages.append({"role": "tool", "tool_call_id": call_id, "content": content})

    # max_rounds exhausted while still wanting tools: force a final text answer.
    data = _post_chat({"model": mdl, "messages": messages, "max_tokens": TOOL_MAX_TOKENS})
    text = _msg(data).get("content") or ""
    emit({"type": "assistant", "text": text})
    return text
