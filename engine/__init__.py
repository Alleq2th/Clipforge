"""ClipForge clipping engine.

Stages, in order:
    ingest  -> asr -> scenes -> moments -> reframe -> captions -> render

Each stage is a plain function with no global state, so it can be unit-tested
and run in a worker process independently.
"""

__all__ = [
    "ingest",
    "asr",
    "scenes",
    "moments",
    "reframe",
    "captions",
    "render",
]
