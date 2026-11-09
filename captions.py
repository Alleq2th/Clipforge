def _t(x):
    h, m, s = int(x // 3600), int(x % 3600 // 60), x % 60
    return f"{h:02}:{m:02}:{s:06.3f}".replace(".", ",")

def make_srt(segs, start, end, path, words=3):
    """
    Builds punchy, word-chunked SRT subtitles (2-3 words per card)
    matching viral short-form video pacing (TikTok / Reels / Shorts).
    Uses genuine word-level timestamps when available; gracefully falls
    back to proportional pacing when only segment-level timings exist.
    """
    n, lines = 1, []

    # 1. First check if any segment inside the range has genuine word-level timestamps
    all_words = []
    has_genuine_words = False
    for s in segs:
        if s.get("end", 0) <= start or s.get("start", 0) >= end:
            continue
        seg_words = s.get("words", [])
        if seg_words and isinstance(seg_words, list) and len(seg_words) > 0:
            has_genuine_words = True
            for w in seg_words:
                w_start = float(w.get("start", s.get("start", 0)))
                w_end = float(w.get("end", s.get("end", 0)))
                w_text = str(w.get("word", "")).strip()
                if w_text and (w_end > start and w_start < end):
                    all_words.append({
                        "text": w_text,
                        "start": max(w_start, start),
                        "end": min(w_end, end)
                    })

    if has_genuine_words and all_words:
        # Group into word chunks of size `words` (default 3 words)
        for i in range(0, len(all_words), words):
            chunk = all_words[i:i + words]
            t1 = _t(chunk[0]["start"] - start)
            t2 = _t(chunk[-1]["end"] - start)
            # Ensure subtitle display is at least 0.25s long for visual legibility
            if chunk[-1]["end"] - chunk[0]["start"] < 0.25:
                t2 = _t(chunk[0]["start"] - start + 0.35)
            text = " ".join(w["text"] for w in chunk).upper()
            lines.append(f"{n}\n{t1} --> {t2}\n{text}\n")
            n += 1
    else:
        # Fallback to segment-level text chunking
        for s in segs:
            s_st = float(s.get("start", 0))
            s_en = float(s.get("end", 0))
            if s_en <= start or s_st >= end:
                continue
            w = s.get("text", "").strip().split()
            if not w:
                continue
            chunks = [w[i:i + words] for i in range(0, len(w), words)]
            a, b = max(s_st, start), min(s_en, end)
            dur = (b - a) / max(len(chunks), 1)
            for i, c in enumerate(chunks):
                t1 = _t(a - start + i * dur)
                t2 = _t(a - start + (i + 1) * dur)
                text = " ".join(c).upper()
                lines.append(f"{n}\n{t1} --> {t2}\n{text}\n")
                n += 1

    if not lines:
        lines = ["1\n00:00:00,000 --> 00:00:01,000\n \n"]

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
