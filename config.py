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

# Safe concurrency limit for rendering independent clips (bounded to protect container memory)
MAX_CLIP_WORKERS = max(1, min(4, int(os.getenv("MAX_CLIP_WORKERS", "2"))))
# Video Export Profiles (resolution, tuned CRF, and loudness normalization)
EXPORT_PROFILES = {
    "540p": {
        "name": "540p",
        "label": "Standard Draft (540p)",
        "resolutions": {
            "9:16": (540, 960),
            "1:1": (540, 540),
            "16:9": (960, 540),
        },
        "crf": 26,
        "preset": "ultrafast",
        "audio_bitrate": "96k",
        "normalize_audio": False,
        "font_scale": 1.0,
    },
    "1080p": {
        "name": "1080p",
        "label": "Full HD Studio (1080p)",
        "resolutions": {
            "9:16": (1080, 1920),
            "1:1": (1080, 1080),
            "16:9": (1920, 1080),
        },
        "crf": 21,
        "preset": "veryfast",
        "audio_bitrate": "192k",
        "normalize_audio": True,
        "font_scale": 1.85,
    },
}
DEFAULT_EXPORT_PROFILE = os.getenv("EXPORT_PROFILE", "540p").strip().lower()
if DEFAULT_EXPORT_PROFILE not in EXPORT_PROFILES:
    DEFAULT_EXPORT_PROFILE = "540p"
