import json, requests
from config import *

def _gemini(p):
    models = [GEMINI_MODEL, "gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro"]
    seen = set()
    last_err = None
    for m in models:
        if not m or m in seen:
            continue
        seen.add(m)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={GEMINI_KEY}"
        try:
            r = requests.post(url, json={"contents": [{"parts": [{"text": p}]}],
                                         "generationConfig": {"responseMimeType": "application/json"}}, timeout=120)
            if r.status_code == 200:
                return r.json()["candidates"][0]["content"]["parts"][0]["text"]
            last_err = f"{m}: HTTP {r.status_code} - {r.text[:150]}"
        except Exception as e:
            last_err = f"{m}: {e}"
    raise RuntimeError(f"Gemini failed ({last_err})")

def _groq(p):
    models = [GROQ_MODEL, "llama-3.3-70b-versatile", "llama-3.1-8b-instant"]
    seen = set()
    last_err = None
    for m in models:
        if not m or m in seen:
            continue
        seen.add(m)
        try:
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_KEY}"},
                json={
                    "model": m,
                    "messages": [{"role": "user", "content": p}],
                    "response_format": {"type": "json_object"},
                },
                timeout=120,
            )
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"]
            last_err = f"{m}: HTTP {r.status_code} - {r.text[:150]}"
        except Exception as e:
            last_err = f"{m}: {e}"
    raise RuntimeError(f"Groq failed ({last_err})")

def _openai_like(base, key, model):
    def f(p):
        r = requests.post(f"{base}/chat/completions", headers={"Authorization": f"Bearer {key}"},
                          json={"model": model, "messages": [{"role": "user", "content": p}],
                                "response_format": {"type": "json_object"}}, timeout=120)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code} - {r.text[:150]}")
        return r.json()["choices"][0]["message"]["content"]
    return f

def providers():
    ps = []
    if GEMINI_KEY: ps.append(("gemini", _gemini))
    if GROQ_KEY: ps.append(("groq", _groq))
    if XAI_KEY: ps.append(("grok", _openai_like("https://api.x.ai/v1", XAI_KEY, XAI_MODEL)))
    return ps

def ask_json(prompt):
    errs = []
    for name, fn in providers():
        try:
            return json.loads(fn(prompt))
        except Exception as e:
            errs.append(f"{name}: {e}")
    raise RuntimeError("All AI providers failed or none set. " + " | ".join(errs))
