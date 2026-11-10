"""LLM provider abstraction.

Any one key is enough. Providers are tried in order and the first that returns
parseable JSON wins. Every failure is collected so the caller can show the user
something actionable instead of "AI failed".
"""
import json
import re

import requests

from config import (
    GEMINI_KEY,
    GEMINI_MODEL,
    GROQ_KEY,
    GROQ_MODEL,
    OPENAI_KEY,
    OPENAI_MODEL,
    XAI_KEY,
    XAI_MODEL,
)


# ------------------------------------------------------------------------ gemini
def _get_gemini_models():
    models = []
    if GEMINI_MODEL:
        models.append(GEMINI_MODEL)
    try:
        r = requests.get(
            f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_KEY}",
            timeout=4,
        )
        if r.status_code == 200:
            for item in r.json().get("models", []):
                if "generateContent" in item.get("supportedGenerationMethods", []):
                    name = item.get("name", "").replace("models/", "")
                    if name and name not in models:
                        models.append(name)
    except Exception:  # noqa: BLE001 - discovery is best effort
        pass
    for fb in ["gemini-flash-latest", "gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-flash-lite"]:
        if fb not in models:
            models.append(fb)
    return models


def _gemini(p):
    models = _get_gemini_models()
    seen, errs = set(), []
    for m in models:
        if not m or m in seen:
            continue
        seen.add(m)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={GEMINI_KEY}"
        try:
            r = requests.post(
                url,
                json={
                    "contents": [{"parts": [{"text": p}]}],
                    "generationConfig": {"responseMimeType": "application/json"},
                },
                timeout=60,
            )
            if r.status_code == 200:
                return r.json()["candidates"][0]["content"]["parts"][0]["text"]
            if r.status_code == 400:
                # Older models reject responseMimeType; retry without it.
                r2 = requests.post(url, json={"contents": [{"parts": [{"text": p}]}]}, timeout=60)
                if r2.status_code == 200:
                    return r2.json()["candidates"][0]["content"]["parts"][0]["text"]
            errs.append(f"{m}: HTTP {r.status_code} - {r.text[:120]}")
        except Exception as e:  # noqa: BLE001
            errs.append(f"{m}: {e}")
        if len(errs) >= 2:
            break
    raise RuntimeError(f"Gemini failed ({'; '.join(errs)})")


# -------------------------------------------------------------------------- groq
def _get_groq_models():
    models = []
    if GROQ_MODEL:
        models.append(GROQ_MODEL)
    try:
        r = requests.get(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {GROQ_KEY}"},
            timeout=4,
        )
        if r.status_code == 200:
            for item in r.json().get("data", []):
                mid = item.get("id", "")
                if mid and not any(
                    x in mid.lower() for x in ["whisper", "guard", "audio", "orpheus"]
                ):
                    if mid not in models:
                        models.append(mid)
    except Exception:  # noqa: BLE001
        pass
    for fb in ["openai/gpt-oss-120b", "openai/gpt-oss-20b",
               "llama-3.3-70b-versatile", "llama-3.1-8b-instant"]:
        if fb not in models:
            models.append(fb)
    return models


def _groq(p):
    models = _get_groq_models()
    seen, errs = set(), []
    for m in models:
        if not m or m in seen:
            continue
        seen.add(m)
        payload = {
            "model": m,
            "messages": [{"role": "user", "content": p}],
            "response_format": {"type": "json_object"},
        }
        try:
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_KEY}"},
                json=payload,
                timeout=60,
            )
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"]
            if r.status_code == 400 and "response_format" in r.text:
                payload.pop("response_format", None)
                r2 = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {GROQ_KEY}"},
                    json=payload,
                    timeout=60,
                )
                if r2.status_code == 200:
                    return r2.json()["choices"][0]["message"]["content"]
            errs.append(f"{m}: HTTP {r.status_code} - {r.text[:120]}")
        except Exception as e:  # noqa: BLE001
            errs.append(f"{m}: {e}")
        if len(errs) >= 2:
            break
    raise RuntimeError(f"Groq failed ({'; '.join(errs)})")


# --------------------------------------------------------- openai / xai (shared)
def _openai_compatible(base_url, api_key, model, prompt):
    """Shared caller for OpenAI-shaped APIs (OpenAI, xAI)."""
    r = requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
        },
        timeout=60,
    )
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} - {r.text[:150]}")
    return r.json()["choices"][0]["message"]["content"]


def _xai(p):
    return _openai_compatible("https://api.x.ai/v1", XAI_KEY, XAI_MODEL, p)


def _openai(p):
    return _openai_compatible("https://api.openai.com/v1", OPENAI_KEY, OPENAI_MODEL, p)


# --------------------------------------------------------------------- registry
def providers():
    ps = []
    if GEMINI_KEY:
        ps.append(("gemini", _gemini))
    if GROQ_KEY:
        ps.append(("groq", _groq))
    if OPENAI_KEY:
        ps.append(("openai", _openai))
    if XAI_KEY:
        ps.append(("xai", _xai))
    return ps


def configured():
    """Names of providers that have a key set."""
    return [name for name, _ in providers()]


# ----------------------------------------------------------------------- parsing
def clean_json(raw):
    """Parses JSON from a model response, tolerating fences and prose."""
    raw = (raw or "").strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    try:
        return json.loads(raw)
    except ValueError:
        m = re.search(r"(\{.*\}|\[.*\])", raw, re.DOTALL)
        if m:
            return json.loads(m.group(1))
        raise


def ask_json(prompt):
    """Returns parsed JSON from the first provider that works."""
    errs = []
    for name, fn in providers():
        try:
            return clean_json(fn(prompt))
        except Exception as e:  # noqa: BLE001
            errs.append(f"{name}: {e}")
    if not errs:
        raise RuntimeError("No AI provider configured (set GEMINI_API_KEY, GROQ_API_KEY, OPENAI_API_KEY or XAI_API_KEY).")
    raise RuntimeError("All AI providers failed. " + " | ".join(errs))
