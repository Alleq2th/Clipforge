def _t(x):
    h, m, s = int(x // 3600), int(x % 3600 // 60), x % 60
    return f"{h:02}:{m:02}:{s:06.3f}".replace(".", ",")

def make_srt(segs, start, end, path, words=4):
    n, lines = 1, []
    for s in segs:
        if s["end"] <= start or s["start"] >= end:
            continue
        w = s["text"].split()
        chunks = [w[i:i + words] for i in range(0, len(w), words)]
        a, b = max(s["start"], start), min(s["end"], end)
        d = (b - a) / max(len(chunks), 1)
        for i, c in enumerate(chunks):
            lines.append(f"{n}
{_t(a - start + i * d)} --> {_t(a - start + (i + 1) * d)}
{' '.join(c).upper()}
")
            n += 1
    if not lines:
        lines = ["1
00:00:00,000 --> 00:00:01,000
 
"]
    open(path, "w", encoding="utf-8").write("
".join(lines))
