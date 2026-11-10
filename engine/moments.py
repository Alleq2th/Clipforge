"""Stage 4 - moment selection (MomentRank).

This is the part that decides whether the product is good. Three ideas make it
work:

1. **Overlapping windows.** A 2-hour podcast is not one prompt. The transcript
   is chunked into overlapping windows so context is never lost at a boundary,
   and every window is scored independently.

2. **A structured schema, not prose.** The model must return start, end, hook
   score, virality score, reason and title per clip. Scores are separate from
   the reason so the UI can show both, and so we can rank honestly.

3. **Word-boundary snapping.** The model's timestamps are approximate. Every
   cut point is snapped to the nearest real word boundary from the ASR, so a
   clip never starts or ends mid-word. This is the single biggest quality win
   over naive implementations.
"""
import json
import math
import re

from config import CLIP_COUNT, MAX_LEN, MIN_LEN
from llm import ask_json

WINDOW_SEC = 360.0     # 6 minutes of transcript per prompt
WINDOW_STEP = 270.0    # 4.5 minute stride -> 90s of overlap

FOCUS = {
    "viral": "irresistible hooks, surprising claims, and stories that hold attention",
    "action": "high-energy moments, hype reactions, and dramatic beats",
    "funny": "comedy, fails, and genuinely funny exchanges",
    "insight": "eye-opening insights, lessons, and quotable wisdom",
    "controversial": "bold opinions, disagreement, and polarising takes",
    "educational": "clear explanations, steps, and actionable takeaways",
}

PROMPT = """You are MomentRank, a clip-selection model for short-form video.

From the transcript window below, find up to {n} moments that would work as
standalone vertical clips of {lo}-{hi} seconds.

What makes a moment score well:
- Hook (30%): the first 3 seconds create a curiosity gap or strong emotion.
- Completeness (30%): a self-contained thought with a beginning and a payoff.
- Emotion (20%): surprise, humour, tension, or a genuinely useful insight.
- Retention (10%): clean sentence boundaries; never cut a speaker mid-thought.
- Shareability (10%): makes someone want to comment or send it to a friend.

Focus for this run: {focus}

Rules:
- start and end are seconds from the beginning of the whole video.
- Every clip must be between {lo} and {hi} seconds.
- Do not return two clips that cover the same moment.
- hook_score and virality_score are integers 0-100. Be honest: most moments
  are 60-80. Reserve 90+ for genuinely exceptional moments.
- reason is one specific sentence about why this moment works. No filler.

Return ONLY JSON:
{{"clips": [{{
  "start": 12.5,
  "end": 45.0,
  "title": "4-7 word title",
  "hook_score": 88,
  "virality_score": 84,
  "reason": "One specific sentence.",
  "topic": "short topic label",
  "category": "Hook|Story|Insight|Comedy|Debate|Tutorial",
  "caption": "A caption that invites a comment.",
  "hashtags": ["#shorts", "#viral"]
}}]}}

Transcript window:
{t}"""


def _window_text(segments):
    """Formats segments as timestamped blocks the model can reason about."""
    if not segments:
        return ""
    blocks, cur_start, cur_end, cur = [], segments[0]["start"], segments[0]["end"], [segments[0]["text"]]
    for s in segments[1:]:
        if (s["end"] - cur_start) > 25.0 or (s["start"] - cur_end) > 2.5:
            blocks.append(f"[{int(cur_start)}-{int(cur_end)}] {' '.join(cur)}")
            cur_start, cur_end, cur = s["start"], s["end"], [s["text"]]
        else:
            cur_end = s["end"]
            cur.append(s["text"])
    blocks.append(f"[{int(cur_start)}-{int(cur_end)}] {' '.join(cur)}")
    return "\n".join(blocks)


def _words_in_range(segments, start, end):
    """All word dicts overlapping [start, end], in order."""
    out = []
    for s in segments:
        if s["end"] <= start or s["start"] >= end:
            continue
        for w in s.get("words") or []:
            if w["end"] > start and w["start"] < end:
                out.append(w)
    out.sort(key=lambda w: w["start"])
    return out


def snap_to_words(segments, start, end, lo, hi, cuts=None):
    """Snaps a candidate to real word boundaries and enforces duration bounds.

    Strategy:
      * start -> the start of the first word at or after `start` (minus a small
        lead-in so the first syllable is not clipped).
      * end   -> the end of the last word at or before `end` (plus a short tail
        so the final consonant is audible).
      * If a scene cut sits within 0.75s of the snapped start, prefer the cut:
        starting on a shot change reads as intentional.
    """
    words = _words_in_range(segments, max(0.0, start - 2.0), end + 2.0)
    if not words:
        return round(start, 3), round(min(end, start + hi), 3)

    # --- start: first word that begins at or after the requested start
    first = next((w for w in words if w["start"] >= start - 0.35), words[0])
    snapped_start = max(0.0, first["start"] - 0.12)

    if cuts:
        from engine.scenes import nearest_cut
        cut = nearest_cut(snapped_start, cuts, tolerance=0.75)
        if cut is not None and cut <= snapped_start + 0.5:
            snapped_start = cut

    # --- end: last word that ends at or before the requested end
    candidates = [w for w in words if w["end"] <= end + 0.6 and w["start"] > snapped_start]
    if not candidates:
        candidates = [w for w in words if w["start"] > snapped_start]
    if not candidates:
        return round(snapped_start, 3), round(min(snapped_start + lo, snapped_start + hi), 3)

    snapped_end = candidates[-1]["end"] + 0.18

    # --- enforce duration without breaking the word boundary we just found
    dur = snapped_end - snapped_start
    if dur > hi:
        # Walk back to the last word that still fits inside hi.
        fitting = [w for w in candidates if (w["end"] + 0.18 - snapped_start) <= hi]
        snapped_end = (fitting[-1]["end"] + 0.18) if fitting else snapped_start + hi
    elif dur < lo:
        # Walk forward to the first word that reaches lo.
        later = [w for w in words if w["start"] > snapped_end and (w["end"] + 0.18 - snapped_start) >= lo]
        if later:
            snapped_end = later[0]["end"] + 0.18
        else:
            snapped_end = min(snapped_start + lo, snapped_start + hi)

    return round(snapped_start, 3), round(snapped_end, 3)


def _dedupe(candidates, target, lo, hi):
    """Ranks by virality, drops overlaps, keeps topic variety, returns chronological."""
    candidates.sort(key=lambda c: int(c.get("virality_score", 0)), reverse=True)
    chosen, topics = [], set()

    for c in candidates:
        s, e = float(c["start"]), float(c["end"])
        if e - s < lo - 1.0 or e - s > hi + 1.0:
            continue
        if any(
            max(0.0, min(e, float(o["end"])) - max(s, float(o["start"]))) > 6.0
            or abs(s - float(o["start"])) < 12.0
            for o in chosen
        ):
            continue
        topic = str(c.get("topic", "")).strip().lower()[:24]
        if topic and topic in topics and len(candidates) > target * 1.5:
            continue
        if topic:
            topics.add(topic)
        chosen.append(c)
        if len(chosen) >= target:
            break

    chosen.sort(key=lambda c: float(c["start"]))
    return chosen


def _heuristic(segments, count, lo, hi):
    """Fallback when no LLM key is configured.

    Picks evenly spaced windows that begin on a sentence start, and labels them
    honestly as heuristic rather than inventing AI scores.
    """
    if not segments:
        return []
    total = max(s["end"] for s in segments)
    if total < lo:
        return []

    n = max(1, min(count, int(total // max(lo, 10))))
    length = min(hi, max(lo, 32.0))
    step = max(length, (total - length) / max(1, n))

    out = []
    for i in range(n):
        raw_start = i * step
        raw_end = min(total, raw_start + length)
        if raw_end - raw_start < lo - 2:
            continue
        s, e = snap_to_words(segments, raw_start, raw_end, lo, hi)
        text = " ".join(
            seg["text"] for seg in segments if seg["end"] > s and seg["start"] < e
        ).strip()
        title = " ".join(text.split()[:7]) or f"Highlight {i + 1}"
        out.append({
            "start": s,
            "end": e,
            "duration": round(e - s, 3),
            "title": title.rstrip(".,!?"),
            "hook_score": 0,
            "virality_score": 0,
            "reason": "Selected by even spacing - no AI key configured, so this is not a scored pick.",
            "topic": "",
            "category": "Highlight",
            "caption": "",
            "hashtags": [],
            "scored": False,
        })
    return out


def select(segments, count=None, min_len=None, max_len=None, focus="viral", cuts=None):
    """Returns ranked, word-snapped clip candidates.

    Each item: start, end, duration, title, hook_score, virality_score, reason,
    topic, category, caption, hashtags, scored.
    """
    if not segments:
        return []

    target = max(1, min(20, int(count or CLIP_COUNT)))
    lo = float(min_len or MIN_LEN)
    hi = float(max_len or MAX_LEN)
    if hi <= lo:
        hi = lo + 30.0

    total = max(s["end"] for s in segments)
    focus_text = FOCUS.get(focus, FOCUS["viral"])

    # --- build overlapping windows
    if total <= WINDOW_SEC:
        windows = [(0.0, total, segments)]
    else:
        windows = []
        steps = math.ceil((total - WINDOW_SEC) / WINDOW_STEP) + 1
        for i in range(steps):
            ws = i * WINDOW_STEP
            we = min(total, ws + WINDOW_SEC)
            segs = [s for s in segments if s["end"] > ws and s["start"] < we]
            if segs and (we - ws) >= lo:
                windows.append((ws, we, segs))

    per_window = max(3, math.ceil((target * 1.6) / max(len(windows), 1)))
    candidates = []

    for ws, we, segs in windows:
        text = _window_text(segs)
        if not text.strip():
            continue
        prompt = PROMPT.format(
            n=min(10, per_window), lo=int(lo), hi=int(hi),
            focus=focus_text, t=text,
        )
        try:
            data = ask_json(prompt)
        except Exception as e:  # noqa: BLE001 - no key / provider down
            print(f"[MOMENTS] window {int(ws)}s: {e}")
            continue

        raw = data.get("clips", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        for c in raw:
            try:
                cs, ce = float(c["start"]), float(c["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if ce <= cs or (ce - cs) < lo - 6.0:
                continue
            if cs < ws - 15.0 or ce > we + 15.0:
                continue
            c["start"], c["end"] = cs, ce
            c["scored"] = True
            candidates.append(c)

    if not candidates:
        print("[MOMENTS] No scored candidates; using heuristic spacing.")
        return _heuristic(segments, target, lo, hi)

    # --- snap every candidate to real word boundaries, then rank
    for c in candidates:
        s, e = snap_to_words(segments, c["start"], c["end"], lo, hi, cuts=cuts)
        c["start"], c["end"] = s, e
        c["duration"] = round(e - s, 3)

    ranked = _dedupe(candidates, target, lo, hi)
    for c in ranked:
        c.setdefault("hook_score", 0)
        c.setdefault("virality_score", 0)
        c.setdefault("category", "Highlight")
        c.setdefault("hashtags", [])
        c.setdefault("caption", "")
        c.setdefault("topic", "")
        c["duration"] = round(float(c["end"]) - float(c["start"]), 3)

    print(f"[MOMENTS] {len(ranked)} clips from {len(candidates)} candidates across {len(windows)} windows")
    return ranked
