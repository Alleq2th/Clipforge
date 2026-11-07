import os
# Keys: set any of these. Tried in order; if one fails (quota/limit), the next is used.
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")
GROQ_KEY = os.getenv("GROQ_API_KEY", "")   # groq.com (free tier)
XAI_KEY = os.getenv("XAI_API_KEY", "")     # x.ai (Grok)
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
XAI_MODEL = os.getenv("XAI_MODEL", "grok-4")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")  # tiny/base/small
WORK_DIR = "work"
OUT_DIR = "output"
CLIP_COUNT = 5
MIN_LEN, MAX_LEN = 20, 60  # seconds
