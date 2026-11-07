import json, re, requests
from config import *

def _get_gemini_models():
    models = []
    if GEMINI_MODEL:
        models.append(GEMINI_MODEL)
    try:
        r = requests.get(
            f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_KEY}",
            timeout=10,
        )
        if r.status_code == 200:
            for item in r.json().get("models", []):
                methods = item.get("supportedGenerationMethods", [])
                if "generateContent" in methods:
                    name = item.get("name", "").replace("models/", "")
                    if name and name not in models:
                        models.append(name)
    except Exception:
        pass
    for fb in ["gemini-flash-latest", "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-pro-latest", "gemini-3.5-flash-lite", "gemini-2.0-flash"]:
        if fb not in models:
            models.append(fb)
    return models

def _gemini(p):
    models = _get_gemini_models()
    seen = set()
    errs = []
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
                timeout=120,
            )
            if r.status_code == 200:
                return r.json()["candidates"][0]["content"]["parts"][0]["text"]
            if r.status_code == 400:
                r2 = requests.post(url, json={"contents": [{"parts": [{"text": p}]}]}, timeout=120)
                if r2.status_code == 200:
                    return r2.json()["candidates"][0]["content"]["parts"][0]["text"]
            errs.append(f"{m}: HTTP {r.status_code} - {r.text[:120]}")
        except Exception as e:
            errs.append(f"{m}: {e}")
        if len(errs) >= 4:
            break
    raise RuntimeError(f"Gemini failed ({'; '.join(errs)})")

def _get_groq_models():
    models = []
    if GROQ_MODEL:
        models.append(GROQ_MODEL)
    try:
        r = requests.get(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {GROQ_KEY}"},
            timeout=10,
        )
        if r.status_code == 200:
            for item in r.json().get("data", []):
                mid = item.get("id", "")
                if mid and not any(x in mid.lower() for x in ["whisper", "guard", "audio", "orpheus"]):
                    if mid not in models:
                        models.append(mid)
    except Exception:
        pass
    for fb in ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.6-27b", "llama-3.3-70b-versatile", "llama-3.1-8b-instant"]:
        if fb not in models:
            models.append(fb)
    return models

def _groq(p):
    models = _get_groq_models()
    seen = set()
    errs = []
    for m in models:
        if not m or m in seen:
            continue
        seen.add(m)
        try:
            payload = {
                "model": m,
                "messages": [{"role": "user", "content": p}],
                "response_format": {"type": "json_object"},
            }
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_KEY}"},
                json=payload,
                timeout=120,
            )
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"]
            if r.status_code == 400 and "response_format" in r.text:
                payload.pop("response_format", None)
                r2 = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {GROQ_KEY}"},
                    json=payload,
                    timeout=120,
                )
                if r2.status_code == 200:
                    return r2.json()["choices"][0]["message"]["content"]
            errs.append(f"{m}: HTTP {r.status_code} - {r.text[:120]}")
        except Exception as e:
            errs.append(f"{m}: {e}")
        if len(errs) >= 4:
            break
    raise RuntimeError(f"Groq failed ({'; '.join(errs)})")

def _openai_like(base, key, model):
    def f(p):
        r = requests.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": p}],
                "response_format": {"type": "json_object"},
            },
            timeout=120,
        )
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code} - {r.text[:150]}")
        return r.json()["choices"][0]["message"]["content"]
    return f

def providers():
    ps = []
    if GEMINI_KEY:
        ps.append(("gemini", _gemini))
    if GROQ_KEY:
        ps.append(("groq", _groq))
    if XAI_KEY:
        ps.append(("grok", _openai_like("https://api.x.ai/v1", XAI_KEY, XAI_MODEL)))
    return ps

def clean_json(raw):
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    try:
        return json.loads(raw)
    except Exception:
        m = re.search(r"(\{.*\}|\[.*\])", raw, re.DOTALL)
        if m:
            return json.loads(m.group(1))
        raise

def ask_json(prompt):
    errs = []
    for name, fn in providers():
        try:
            return clean_json(fn(prompt))
        except Exception as e:
            errs.append(f"{name}: {e}")
    raise RuntimeError("All AI providers failed or none set. " + " | ".join(errs))
