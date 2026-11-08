def _t(x):
    h, m, s = int(x // 3600), int(x % 3600 // 60), x % 60
    return f"{h:02}:{m:02}:{s:06.3f}".replace(".", ",")

def make_srt(segs, start, end, path, words=3):
    """
    Builds punchy, word-chunked SRT subtitles (2-3 words per card)
    matching viral short-form video pacing (TikTok / Reels / Shorts).
    """
    n, lines = 1, []
    for s in segs:
        if s["end"] <= start or s["start"] >= end:
            continue
        w = s["text"].strip().split()
        if not w:
            continue
        chunks = [w[i:i + words] for i in range(0, len(w), words)]
        a, b = max(s["start"], start), min(s["end"], end)
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
