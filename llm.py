import json, requests
from config import *

def _gemini(p):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_KEY}"
    r = requests.post(url, json={"contents": [{"parts": [{"text": p}]}],
                                 "generationConfig": {"responseMimeType": "application/json"}}, timeout=120)
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]

def _openai_like(base, key, model):
    def f(p):
        r = requests.post(f"{base}/chat/completions", headers={"Authorization": f"Bearer {key}"},
                          json={"model": model, "messages": [{"role": "user", "content": p}],
                                "response_format": {"type": "json_object"}}, timeout=120)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    return f

def providers():
    ps = []
    if GEMINI_KEY: ps.append(("gemini", _gemini))
    if GROQ_KEY: ps.append(("groq", _openai_like("https://api.groq.com/openai/v1", GROQ_KEY, GROQ_MODEL)))
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
