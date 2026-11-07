import os
# Keys: set any of these. Tried in order; if one fails (quota/limit), the next is used.
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GROQ_KEY = os.getenv("GROQ_API_KEY", "").strip()   # groq.com (free tier)
XAI_KEY = os.getenv("XAI_API_KEY", "").strip()     # x.ai (Grok)
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip()
XAI_MODEL = os.getenv("XAI_MODEL", "grok-4").strip()
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base").strip()  # tiny/base/small
WORK_DIR = "work"
OUT_DIR = "output"
CLIP_COUNT = 5
MIN_LEN, MAX_LEN = 20, 60  # seconds
