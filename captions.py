"""
Advanced Subtitle Generation Engine for ClipForge.
Supports:
1. Styled ASS (Advanced SubStation Alpha) captions with animated karaoke word highlights
   matching modern viral short-form editing standards (MrBeast, TikTok, Reels, Shorts).
2. Word-level timestamp synchronization and punchy 2-3 word card grouping.
3. Proportional rhythmic fallback when only segment-level timings exist.
4. Backwards-compatible make_srt() for standard SRT output and existing test suites.
5. Preserves and extends all caption presets (beasty, karaoke, classic, cyber, clean_minimal, etc.).
"""

def _t(x):
    """Formats seconds into SRT timestamp string HH:MM:SS,mmm."""
    h, m, s = int(x // 3600), int(x % 3600 // 60), x % 60
    return f"{h:02}:{m:02}:{s:06.3f}".replace(".", ",")

def _t_ass(x):
    """Formats seconds into ASS timestamp string H:MM:SS.cc (centiseconds)."""
    x = max(0.0, float(x))
    total_cs = int(round(x * 100))
    h = total_cs // 360000
    rem = total_cs % 360000
    m = rem // 6000
    rem = rem % 6000
    s = rem // 100
    cs = rem % 100
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

def sanitize_ass_text(text):
    """Escapes special ASS control characters from subtitle text."""
    if not text:
        return ""
    s = str(text).strip()
    s = s.replace("{", "(").replace("}", ")").replace("\\", "/")
    s = " ".join(s.split())
    return s.upper()

# Detailed styling dictionary for all presets in ASS format
# Colors are in ASS BGR format: &HAABBGGRR (00 = fully opaque)
ASS_PRESETS = {
    # Beast Yellow: viral high-contrast yellow highlight on crisp white base with heavy black outline
    "beasty": {
        "primary": "&H0000FFFF",      # Bright Yellow (Highlight)
        "secondary": "&H00FFFFFF",    # Opaque White (Base)
        "outline": "&H00000000",      # Deep Black Outline
        "back": "&H80000000",         # Soft Drop Shadow
        "bold": 1,
        "font_mult": 1.05,
        "outline_mult": 1.1,
        "shadow_mult": 1.2,
        "karaoke_tag": "k",
    },
    # Karaoke Green: vibrant lime-green karaoke highlight
    "karaoke": {
        "primary": "&H0000FF00",      # Vibrant Green
        "secondary": "&H00FFFFFF",
        "outline": "&H00000000",
        "back": "&H80000000",
        "bold": 1,
        "font_mult": 1.0,
        "outline_mult": 1.0,
        "shadow_mult": 1.0,
        "karaoke_tag": "k",
    },
    # Classic White: crisp white with warm golden amber highlight
    "classic": {
        "primary": "&H0000E5FF",      # Warm Amber / Gold
        "secondary": "&H00FFFFFF",    # Crisp White
        "outline": "&H00000000",      # Clean Black Outline
        "back": "&H80000000",
        "bold": 1,
        "font_mult": 1.0,
        "outline_mult": 0.85,
        "shadow_mult": 0.8,
        "karaoke_tag": "k",
    },
    # Cyber Pink: neon hot pink/magenta highlight on electric cyan/white
    "cyber": {
        "primary": "&H00FF00FF",      # Hot Neon Pink / Magenta
        "secondary": "&H00FFFFFF",    # Bright White
        "outline": "&H00330033",      # Deep Midnight Purple Outline
        "back": "&H80000000",
        "bold": 1,
        "font_mult": 1.05,
        "outline_mult": 1.1,
        "shadow_mult": 1.4,
        "karaoke_tag": "k",
    },
    # Clean Minimal: refined modern sans with subtle semi-transparent border
    "clean_minimal": {
        "primary": "&H0000FFFF",      # Subtle Bright Yellow
        "secondary": "&H00FFFFFF",    # Pure White
        "outline": "&H80000000",      # Translucent Dark Edge
        "back": "&H00000000",         # No heavy shadow
        "bold": 1,
        "font_mult": 0.95,
        "outline_mult": 0.5,
        "shadow_mult": 0.0,
        "karaoke_tag": "k",
    },
    "minimal": {
        "primary": "&H0000FFFF",
        "secondary": "&H00FFFFFF",
        "outline": "&H80000000",
        "back": "&H00000000",
        "bold": 1,
        "font_mult": 0.95,
        "outline_mult": 0.5,
        "shadow_mult": 0.0,
        "karaoke_tag": "k",
    },
    # Bold Creator: oversized typography with thick stroke for mobile screens
    "bold_creator": {
        "primary": "&H0000FFFF",      # High-Voltage Yellow
        "secondary": "&H00FFFFFF",
        "outline": "&H00000000",      # Extra Heavy Outline
        "back": "&H80000000",
        "bold": 1,
        "font_mult": 1.15,
        "outline_mult": 1.25,
        "shadow_mult": 1.5,
        "karaoke_tag": "k",
    },
    # Highlighted / Keywords: electric green focus
    "highlighted": {
        "primary": "&H0000FF00",      # Electric Green
        "secondary": "&H00FFFFFF",
        "outline": "&H00000000",
        "back": "&H80000000",
        "bold": 1,
        "font_mult": 1.02,
        "outline_mult": 1.0,
        "shadow_mult": 1.0,
        "karaoke_tag": "k",
    },
    "keywords": {
        "primary": "&H0000FF00",
        "secondary": "&H00FFFFFF",
        "outline": "&H00000000",
        "back": "&H80000000",
        "bold": 1,
        "font_mult": 1.02,
        "outline_mult": 1.0,
        "shadow_mult": 1.0,
        "karaoke_tag": "k",
    },
    # Podcast: warm studio slate & amber palette
    "podcast": {
        "primary": "&H0000D4FF",      # Warm Amber / Studio Gold
        "secondary": "&H00E0E0E0",    # Off-White Slate
        "outline": "&H00101010",      # Dark Charcoal
        "back": "&H80000000",
        "bold": 1,
        "font_mult": 0.98,
        "outline_mult": 0.85,
        "shadow_mult": 0.8,
        "karaoke_tag": "k",
    },
    # Cinematic: subtle gold on refined soft white with lowered margin
    "cinematic": {
        "primary": "&H0000C8FF",      # Cinematic Warm Gold
        "secondary": "&H00F0F0F0",    # Soft Off-White
        "outline": "&H60000000",      # Gentle Translucent Stroke
        "back": "&H00000000",
        "bold": 1,
        "font_mult": 0.88,
        "outline_mult": 0.65,
        "shadow_mult": 0.5,
        "margin_v_mult": 0.75,
        "karaoke_tag": "k",
    },
    # Social Bold: electric amber/gold punch
    "social_bold": {
        "primary": "&H0000E5FF",      # Electric Gold / Vivid Amber
        "secondary": "&H00FFFFFF",    # Crisp White
        "outline": "&H00000000",
        "back": "&H80000000",
        "bold": 1,
        "font_mult": 1.08,
        "outline_mult": 1.15,
        "shadow_mult": 1.4,
        "karaoke_tag": "k",
    },
    # Pop Outline: thick outline, bigger words, yellow highlight
    "pop_outline": {
        "primary": "&H0000FFFF", "secondary": "&H00FFFFFF", "outline": "&H00000000", "back": "&H80000000",
        "bold": 1, "font_mult": 1.15, "outline_mult": 1.4, "shadow_mult": 0.6, "karaoke_tag": "k",
    },
    # Minimal White: quiet, light weight, white highlight with grey base
    "minimal_white": {
        "primary": "&H00FFFFFF", "secondary": "&H00B4B4B4", "outline": "&H00000000", "back": "&H60000000",
        "bold": 0, "font_mult": 0.95, "outline_mult": 0.8, "shadow_mult": 0.5, "karaoke_tag": "k",
    },
    # Bold Cyan: cool cyan highlight, medium outline
    "bold_cyan": {
        "primary": "&H00EED322", "secondary": "&H00FFFFFF", "outline": "&H00000000", "back": "&H80000000",
        "bold": 1, "font_mult": 1.1, "outline_mult": 1.2, "shadow_mult": 1.0, "karaoke_tag": "k",
    },
    # Orange Glow: warm orange highlight with a soft glow shadow
    "orange_glow": {
        "primary": "&H00008CFF", "secondary": "&H00FFFFFF", "outline": "&H00000000", "back": "&H80000000",
        "bold": 1, "font_mult": 1.05, "outline_mult": 1.0, "shadow_mult": 2.0, "karaoke_tag": "k",
    },
    # Pink Pop: playful pink highlight, larger shadow
    "pink_pop": {
        "primary": "&H00D84FFF", "secondary": "&H00FFFFFF", "outline": "&H00000000", "back": "&H80000000",
        "bold": 1, "font_mult": 1.12, "outline_mult": 1.25, "shadow_mult": 1.3, "karaoke_tag": "k",
    },
}

def get_ass_dimensions(aspect_ratio="9:16", export_profile=None):
    """Returns (width, height, default_margin_v) based on aspect ratio and export profile."""
    from cut import resolve_export_profile
    ratio = aspect_ratio if aspect_ratio in ("9:16", "1:1", "16:9") else "9:16"
    prof = resolve_export_profile(export_profile)
    w, h = prof["resolutions"][ratio]
    if ratio == "9:16":
        margin_v = round(280 * (h / 1920.0))
    elif ratio == "1:1":
        margin_v = round(120 * (h / 1080.0))
    else:
        margin_v = round(80 * (h / 1080.0))
    return w, h, margin_v

def make_ass(segs, start, end, path, style_key="beasty", aspect_ratio="9:16", export_profile=None, words=3, animation_mode="karaoke", highlight_color=None):
    """
    Builds styled Advanced SubStation Alpha (.ass) subtitles with animated
    karaoke word highlights matching viral short-form pacing.
    """
    w, h, def_margin_v = get_ass_dimensions(aspect_ratio, export_profile)
    is_1080p = (h >= 1080 and w >= 1080)

    preset = ASS_PRESETS.get(style_key, ASS_PRESETS["beasty"])
    if highlight_color and str(highlight_color).startswith("&H"):
        preset = dict(preset, primary=highlight_color)

    # Base typography sizing cleanly scaled by canvas dimensions
    scale_factor = h / 960.0
    base_font_size = round((32 if aspect_ratio == "9:16" else (24 if aspect_ratio == "1:1" else 21)) * scale_factor)
    base_outline = round(2.4 * scale_factor, 1)
    base_shadow = round(0.8 * scale_factor, 1)

    font_size = round(base_font_size * preset.get("font_mult", 1.0))
    outline = round(base_outline * preset.get("outline_mult", 1.0), 1)
    shadow = round(base_shadow * preset.get("shadow_mult", 1.0), 1)
    margin_v = round(def_margin_v * preset.get("margin_v_mult", 1.0))
    margin_lr = max(12, round(20 * scale_factor))

    k_tag = "kf" if animation_mode == "sweep" else preset.get("karaoke_tag", "k")
    style_name = style_key if style_key in ASS_PRESETS else "beasty"

    # Assemble ASS header & style definition
    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {w}",
        f"PlayResY: {h}",
        "ScaledBorderAndShadow: yes",
        "WrapStyle: 0",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: {style_name},Arial,{font_size},{preset['primary']},{preset['secondary']},{preset['outline']},{preset['back']},{preset['bold']},0,0,0,100,100,0,0,1,{outline},{shadow},2,{margin_lr},{margin_lr},{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    events = []

    # 1. Check for genuine word-level timestamps
    all_words = []
    has_genuine_words = False
    for s in segs:
        if s.get("end", 0) <= start or s.get("start", 0) >= end:
            continue
        seg_words = s.get("words", [])
        if seg_words and isinstance(seg_words, list) and len(seg_words) > 0:
            has_genuine_words = True
            for wd in seg_words:
                w_start = float(wd.get("start", s.get("start", 0)))
                w_end = float(wd.get("end", s.get("end", 0)))
                w_text = sanitize_ass_text(wd.get("word", ""))
                if w_text and (w_end > start and w_start < end):
                    all_words.append({
                        "text": w_text,
                        "start": max(w_start, start),
                        "end": min(w_end, end)
                    })

    if has_genuine_words and all_words:
        # Group into word chunks (cards of `words` length)
        for i in range(0, len(all_words), words):
            chunk = all_words[i:i + words]
            t1 = max(0.0, chunk[0]["start"] - start)
            t2 = max(t1 + 0.35, chunk[-1]["end"] - start)

            # Bridge micro-pause to next chunk if close (< 0.25s) to eliminate visual flicker
            if i + words < len(all_words):
                next_start = max(0.0, all_words[i + words]["start"] - start)
                if 0 < next_start - t2 <= 0.25:
                    t2 = next_start

            # Build karaoke tags for each word
            k_parts = []
            for j, wd in enumerate(chunk):
                if j < len(chunk) - 1:
                    span = max(0.05, chunk[j + 1]["start"] - wd["start"])
                else:
                    span = max(0.05, wd["end"] - wd["start"])
                dur_cs = max(1, int(round(span * 100)))
                k_parts.append(f"{{\\{k_tag}{dur_cs}}}{wd['text']}")

            k_text = " ".join(k_parts)
            events.append(f"Dialogue: 0,{_t_ass(t1)},{_t_ass(t2)},{style_name},,0,0,0,,{k_text}")
    else:
        # Proportional rhythmic fallback for segment-level timings
        for s in segs:
            s_st = float(s.get("start", 0))
            s_en = float(s.get("end", 0))
            if s_en <= start or s_st >= end:
                continue
            raw_words = [sanitize_ass_text(w) for w in s.get("text", "").split() if w.strip()]
            if not raw_words:
                continue
            chunks = [raw_words[i:i + words] for i in range(0, len(raw_words), words)]
            a, b = max(s_st, start), min(s_en, end)
            dur = (b - a) / max(len(chunks), 1)
            for i, c in enumerate(chunks):
                t1 = a - start + i * dur
                t2 = a - start + (i + 1) * dur
                if t2 - t1 < 0.35:
                    t2 = t1 + 0.35
                word_dur_cs = max(1, int(round((dur / len(c)) * 100)))
                k_parts = [f"{{\\{k_tag}{word_dur_cs}}}{word}" for word in c]
                k_text = " ".join(k_parts)
                events.append(f"Dialogue: 0,{_t_ass(t1)},{_t_ass(t2)},{style_name},,0,0,0,,{k_text}")

    if not events:
        events.append(f"Dialogue: 0,0:00:00.00,0:00:01.00,{style_name},,0,0,0,, ")

    full_content = "\n".join(header + events) + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(full_content)
    return path

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
