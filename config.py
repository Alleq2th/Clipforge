"""ClipForge configuration.

Every knob is an environment variable so the same image runs locally and on a host.
Nothing here reaches out to the network at import time.
"""
import os


def _int(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _float(name, default):
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- LLM providers
# Tried in order. Any one of them is enough to run MomentRank.
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GROQ_KEY = os.getenv("GROQ_API_KEY", "").strip()      # also used for cloud ASR
XAI_KEY = os.getenv("XAI_API_KEY", "").strip()
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "").strip()

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip()
XAI_MODEL = os.getenv("XAI_MODEL", "grok-4").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()

# ------------------------------------------------------------------------ ASR
# tiny | base | small | medium | large-v3
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base").strip()
WHISPER_COMPUTE = os.getenv("WHISPER_COMPUTE", "int8").strip()
WHISPER_BEAM = _int("WHISPER_BEAM", 1)
# Prefer the cloud ASR when a Groq key exists: ~10x faster than local CPU.
ASR_PREFER_CLOUD = os.getenv("ASR_PREFER_CLOUD", "1") not in ("0", "false", "False")

# --------------------------------------------------------------------- storage
WORK_DIR = os.getenv("WORK_DIR", "work")
OUT_DIR = os.getenv("OUT_DIR", "output")

# ------------------------------------------------------------------- pipeline
CLIP_COUNT = _int("CLIP_COUNT", 5)
MIN_LEN = _int("MIN_LEN", 20)
MAX_LEN = _int("MAX_LEN", 60)
MAX_VIDEOS_PER_JOB = _int("MAX_VIDEOS_PER_JOB", 5)
MAX_CONCURRENT_JOBS = _int("MAX_CONCURRENT_JOBS", 2)
MAX_CONCURRENT_RENDERS = _int("MAX_CONCURRENT_RENDERS", 2)

# --------------------------------------------------------------------- render
RENDER_WIDTH = _int("RENDER_WIDTH", 1080)
RENDER_HEIGHT = _int("RENDER_HEIGHT", 1920)
RENDER_CRF = _int("RENDER_CRF", 18)          # 18 = visually lossless for social
RENDER_PRESET = os.getenv("RENDER_PRESET", "medium").strip()
RENDER_AUDIO_BITRATE = os.getenv("RENDER_AUDIO_BITRATE", "192k").strip()

# ------------------------------------------------------------------- reframe
# Frames sampled per second when building the crop path. 6 is plenty for a
# talking head and keeps a 60s clip under ~400 detections.
REFRAME_SAMPLE_FPS = _float("REFRAME_SAMPLE_FPS", 6.0)
# Exponential smoothing factor for the crop path (0 = frozen, 1 = raw/jittery).
REFRAME_SMOOTHING = _float("REFRAME_SMOOTHING", 0.18)
# How much of the frame height the subject's head should sit below the top.
REFRAME_HEADROOM = _float("REFRAME_HEADROOM", 0.18)

# ------------------------------------------------------------------- captions
DEFAULT_CAPTION_STYLE = os.getenv("DEFAULT_CAPTION_STYLE", "bold_yellow").strip()
CAPTION_FONT = os.getenv("CAPTION_FONT", "Inter").strip()

# ------------------------------------------------------------------ retention
STORAGE_BUDGET_MB = _float("STORAGE_BUDGET_MB", 400.0)
MAX_SAVED_JOBS = _int("MAX_SAVED_JOBS", 6)
