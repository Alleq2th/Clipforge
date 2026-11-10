"""Stage 6 - captions.

ASS, not SRT. SRT cannot do karaoke highlighting, per-word colour, or precise
positioning, and burning SRT through ffmpeg's `subtitles` filter gives you the
same three ugly presets everyone else ships.

ASS gives us:
  * word-by-word karaoke (\\k tags) driven by real ASR word timings
  * a highlight colour that sweeps across the active word
  * proper outline + shadow so text is readable over any footage
  * exact positioning in the safe area, per aspect ratio

Styles are data, not code: each one is a dict of ASS style fields plus a
highlight colour, so adding a style is a few lines.
"""
import os

# ASS colours are &HAABBGGRR (alpha, blue, green, red) - not RGB. This trips up
# everyone once; the helper below keeps the call sites readable.
def _ass_color(hex_rgb, alpha=0):
    h = hex_rgb.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


# Each style: font, size (at 1080x1920), weight, colours, outline, shadow,
# margin from the bottom, and how many words to show at once.
STYLES = {
    "bold_yellow": {
        "label": "Bold Yellow",
        "font": "Inter", "size": 96, "bold": True, "italic": False,
        "primary": "#FFFFFF", "highlight": "#FFD400",
        "outline": "#000000", "outline_w": 7, "shadow": 3,
        "margin_v": 300, "words_per_card": 3, "uppercase": True,
        "swatch": "#FFD400",
    },
    "clean_white": {
        "label": "Clean White",
        "font": "Inter", "size": 84, "bold": True, "italic": False,
        "primary": "#FFFFFF", "highlight": "#FFFFFF",
        "outline": "#000000", "outline_w": 5, "shadow": 2,
        "margin_v": 300, "words_per_card": 4, "uppercase": False,
        "swatch": "#FFFFFF",
    },
    "karaoke_green": {
        "label": "Karaoke Green",
        "font": "Inter", "size": 92, "bold": True, "italic": False,
        "primary": "#FFFFFF", "highlight": "#22E06B",
        "outline": "#000000", "outline_w": 7, "shadow": 3,
        "margin_v": 300, "words_per_card": 3, "uppercase": True,
        "swatch": "#22E06B",
    },
    "editorial_serif": {
        "label": "Editorial Serif",
        "font": "DejaVu Serif", "size": 78, "bold": True, "italic": False,
        "primary": "#FFFFFF", "highlight": "#FFD400",
        "outline": "#000000", "outline_w": 4, "shadow": 2,
        "margin_v": 300, "words_per_card": 5, "uppercase": False,
        "swatch": "#E8E2D4",
    },
    "boxed": {
        "label": "Boxed",
        "font": "Inter", "size": 80, "bold": True, "italic": False,
        "primary": "#0B0D12", "highlight": "#0B0D12",
        "outline": "#FFFFFF", "outline_w": 0, "shadow": 0,
        "margin_v": 300, "words_per_card": 3, "uppercase": True,
        "swatch": "#FFFFFF", "box": "#FFFFFF",
    },
    "neon_pink": {
        "label": "Neon Pink",
        "font": "Inter", "size": 94, "bold": True, "italic": False,
        "primary": "#FFFFFF", "highlight": "#FF3D8B",
        "outline": "#000000", "outline_w": 8, "shadow": 4,
        "margin_v": 300, "words_per_card": 3, "uppercase": True,
        "swatch": "#FF3D8B",
    },
    "minimal_lower": {
        "label": "Minimal Lower",
        "font": "Inter", "size": 64, "bold": False, "italic": False,
        "primary": "#FFFFFF", "highlight": "#FFFFFF",
        "outline": "#000000", "outline_w": 3, "shadow": 1,
        "margin_v": 220, "words_per_card": 6, "uppercase": False,
        "swatch": "#9AA4B2",
    },
}

DEFAULT_STYLE = "bold_yellow"


def _ts(seconds):
    """ASS timestamp: H:MM:SS.cc (centiseconds, not milliseconds)."""
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _escape(text):
    """ASS treats { } as override blocks and \\ as an escape."""
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace("{", "(")
        .replace("}", ")")
        .replace("\n", " ")
        .strip()
    )


def _words_for_range(segments, start, end):
    """Word dicts inside [start, end], rebased to clip-local time."""
    words = []
    for s in segments:
        if s["end"] <= start or s["start"] >= end:
            continue
        for w in s.get("words") or []:
            if w["end"] <= start or w["start"] >= end:
                continue
            text = str(w.get("word", "")).strip()
            if not text:
                continue
            words.append({
                "text": text,
                "start": max(0.0, float(w["start"]) - start),
                "end": min(end - start, float(w["end"]) - start),
            })
    words.sort(key=lambda w: w["start"])
    return words


def _synthetic_words(segments, start, end):
    """Fallback when the ASR gave no word timings: spread words evenly."""
    text = " ".join(
        s["text"] for s in segments if s["end"] > start and s["start"] < end
    ).strip()
    tokens = text.split()
    if not tokens:
        return []
    span = max(0.5, end - start)
    per = span / len(tokens)
    return [
        {"text": t, "start": i * per, "end": (i + 1) * per}
        for i, t in enumerate(tokens)
    ]


def build_ass(segments, start, end, style="bold_yellow", width=1080, height=1920):
    """Returns the ASS document as a string.

    Args:
        segments: ASR segments with word timings.
        start, end: clip bounds in source time.
        style: key from STYLES.
        width, height: output resolution, used for PlayRes and margins.
    """
    st = STYLES.get(style) or STYLES[DEFAULT_STYLE]
    words = _words_for_range(segments, start, end) or _synthetic_words(segments, start, end)

    primary = _ass_color(st["primary"])
    highlight = _ass_color(st["highlight"])
    outline = _ass_color(st["outline"])
    back = _ass_color("#000000", alpha=0x60)

    # Scale the style's 1080x1920 design size to the actual output.
    size = max(18, int(round(st["size"] * (width / 1080.0))))
    margin_v = max(20, int(round(st["margin_v"] * (height / 1920.0))))
    outline_w = max(0, int(round(st["outline_w"] * (width / 1080.0))))

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: CF,{st['font']},{size},{primary},{highlight},{outline},{back},{-1 if st['bold'] else 0},{-1 if st['italic'] else 0},0,0,100,100,0,0,{1 if st.get('box') else 1},{outline_w},{st['shadow']},2,60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    if not words:
        return header

    per_card = max(1, int(st["words_per_card"]))
    lines = []

    for i in range(0, len(words), per_card):
        card = words[i:i + per_card]
        card_start = card[0]["start"]
        card_end = max(card[-1]["end"], card_start + 0.25)

        # Build the karaoke line. \k durations are in centiseconds and must sum
        # to the card duration, so the highlight lands on the right word.
        parts = []
        for j, w in enumerate(card):
            dur_cs = max(1, int(round((w["end"] - w["start"]) * 100)))
            text = _escape(w["text"])
            if st["uppercase"]:
                text = text.upper()
            # \kf = smooth sweep; \k = hard switch. Sweep reads better.
            parts.append(f"{{\\kf{dur_cs}}}{text}")

        body = " ".join(parts)

        # Boxed style: draw a filled box behind the text instead of an outline.
        if st.get("box"):
            box = _ass_color(st["box"])
            body = f"{{\\bord0\\shad0\\3c{box}\\4c{box}\\b1}}{body}"

        lines.append(
            f"Dialogue: 0,{_ts(card_start)},{_ts(card_end)},CF,,0,0,0,,{body}"
        )

    return header + "\n".join(lines) + "\n"


def write_ass(segments, start, end, path, style="bold_yellow", width=1080, height=1920):
    """Writes the ASS file and returns its path."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(build_ass(segments, start, end, style=style, width=width, height=height))
    return path


def style_list():
    """Public style metadata for the UI."""
    return [
        {"id": k, "label": v["label"], "swatch": v["swatch"]}
        for k, v in STYLES.items()
    ]
