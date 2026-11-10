import os
import re
import subprocess
from config import OUT_DIR, EXPORT_PROFILES, DEFAULT_EXPORT_PROFILE
from captions import make_srt
from process_manager import run_tracked_subprocess

STYLES = {
    # Original presets
    "beasty": "FontName=Arial,FontSize=13,Bold=1,PrimaryColour=&H0000FFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=3,Shadow=1,Alignment=2,MarginV={margin_v}",
    "karaoke": "FontName=Arial,FontSize=13,Bold=1,PrimaryColour=&H0000FF00,OutlineColour=&H00000000,BorderStyle=1,Outline=3,Shadow=1,Alignment=2,MarginV={margin_v}",
    "classic": "FontName=Arial,FontSize=13,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2.5,Shadow=1,Alignment=2,MarginV={margin_v}",
    "cyber": "FontName=Arial,FontSize=13,Bold=1,PrimaryColour=&H00FF00FF,OutlineColour=&H00000000,BorderStyle=1,Outline=3,Shadow=1,Alignment=2,MarginV={margin_v}",
    # Phase 3 professional presets
    "clean_minimal": "FontName=Arial,FontSize=12,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,BorderStyle=1,Outline=1.5,Shadow=1,Alignment=2,MarginV={margin_v}",
    "minimal": "FontName=Arial,FontSize=12,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,BorderStyle=1,Outline=1.5,Shadow=1,Alignment=2,MarginV={margin_v}",
    "bold_creator": "FontName=Arial,FontSize=14,Bold=1,PrimaryColour=&H0000FFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=3.5,Shadow=1.5,Alignment=2,MarginV={margin_v}",
    "highlighted": "FontName=Arial,FontSize=13,Bold=1,PrimaryColour=&H0000FF00,OutlineColour=&H00000000,BorderStyle=1,Outline=3,Shadow=1,Alignment=2,MarginV={margin_v}",
    "keywords": "FontName=Arial,FontSize=13,Bold=1,PrimaryColour=&H0000FF00,OutlineColour=&H00000000,BorderStyle=1,Outline=3,Shadow=1,Alignment=2,MarginV={margin_v}",
    "podcast": "FontName=Arial,FontSize=13,Bold=1,PrimaryColour=&H00E0E0E0,OutlineColour=&H00101010,BorderStyle=1,Outline=2.5,Shadow=1,Alignment=2,MarginV={margin_v}",
    "cinematic": "FontName=Arial,FontSize=11,Bold=1,PrimaryColour=&H00F0F0F0,OutlineColour=&H00000000,BorderStyle=1,Outline=1.5,Shadow=1,Alignment=2,MarginV={margin_v}",
    "social_bold": "FontName=Arial,FontSize=14,Bold=1,PrimaryColour=&H0000E5FF,OutlineColour=&H00000000,BorderStyle=1,Outline=3,Shadow=1.5,Alignment=2,MarginV={margin_v}",
}

def check_has_audio(video_path):
    """Checks whether the video source file contains at least one audio stream."""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=codec_type",
            "-of", "csv=p=0",
            video_path
        ]
        out = subprocess.check_output(cmd, timeout=5).decode().strip()
        return bool(out)
    except Exception:
        return False

def resolve_export_profile(profile_name=None):
    """Resolves export profile configuration dict with safe fallback to 540p."""
    key = (profile_name or DEFAULT_EXPORT_PROFILE or "540p").strip().lower()
    if any(p in key for p in ("1080", "hd", "high", "premium", "full_hd")):
        return EXPORT_PROFILES["1080p"]
    return EXPORT_PROFILES.get(key, EXPORT_PROFILES["540p"])

def scale_style_for_1080p(style_str, font_scale=1.85, outline_scale=1.8):
    """Scales subtitle font size and outline for high-resolution 1080p rendering."""
    def repl_font(m):
        val = float(m.group(1)) * font_scale
        return f"FontSize={round(val)}"
    def repl_outline(m):
        val = float(m.group(1)) * outline_scale
        return f"Outline={round(val, 1)}"
    s = re.sub(r"FontSize=(\d+(?:\.\d+)?)", repl_font, style_str)
    s = re.sub(r"Outline=(\d+(?:\.\d+)?)", repl_outline, s)
    return s

def get_filter_complex(aspect_ratio, caption_style, srt_filename="clip.srt", framing="crop", x_norm=0.5, y_norm=0.5, export_profile=None):
    ratio = aspect_ratio if aspect_ratio in ("9:16", "1:1", "16:9") else "9:16"
    has_captions = caption_style and caption_style.lower() not in ("none", "off", "false", "")

    profile = resolve_export_profile(export_profile)
    is_1080p = profile["name"] == "1080p"

    if is_1080p:
        margin_v = 280 if ratio == "9:16" else (120 if ratio == "1:1" else 80)
    else:
        margin_v = 140 if ratio == "9:16" else (60 if ratio == "1:1" else 40)

    style_key = caption_style if caption_style in STYLES else "beasty"
    raw_style = STYLES[style_key] if has_captions else ""
    if has_captions and is_1080p:
        raw_style = scale_style_for_1080p(raw_style, font_scale=profile.get("font_scale", 1.85))

    style_str = raw_style.format(margin_v=margin_v) if has_captions else ""
    sub_filter = f",subtitles={srt_filename}:force_style='{style_str}'" if has_captions else ""

    cx = max(0.05, min(0.95, float(x_norm)))
    cy = max(0.05, min(0.95, float(y_norm)))
    is_smart = framing in ("smart", "face", "auto")

    w, h = profile["resolutions"][ratio]

    if ratio == "1:1":
        # Square (540x540 or 1080x1080)
        if framing == "blur":
            blur_w, blur_h = (270, 270) if is_1080p else (135, 135)
            vf = (
                "[0:v]split[a][b];"
                f"[a]scale={blur_w}:{blur_h}:force_original_aspect_ratio=increase,crop={blur_w}:{blur_h},boxblur=4:1,scale={w}:{h}[bg];"
                f"[b]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black@0[fg];"
                f"[bg][fg]overlay=0:0{sub_filter}"
            )
        elif is_smart and (abs(cx - 0.5) > 0.02 or abs(cy - 0.5) > 0.02):
            x_expr = f"max(0,min(iw-ow,({cx:.4f}*iw)-(ow/2)))"
            y_expr = f"max(0,min(ih-oh,({cy:.4f}*ih)-(oh/2)))"
            vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}:x='{x_expr}':y='{y_expr}'{sub_filter}"
        else:
            vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}{sub_filter}"

    elif ratio == "16:9":
        # Landscape (960x540 or 1920x1080)
        vf = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black{sub_filter}"

    else:
        # 9:16 Vertical (540x960 or 1080x1920)
        if framing == "blur":
            blur_w, blur_h = (270, 480) if is_1080p else (135, 240)
            vf = (
                "[0:v]split[a][b];"
                f"[a]scale={blur_w}:{blur_h}:force_original_aspect_ratio=increase,crop={blur_w}:{blur_h},boxblur=4:1,scale={w}:{h}[bg];"
                f"[b]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black@0[fg];"
                f"[bg][fg]overlay=0:0{sub_filter}"
            )
        elif framing == "fit":
            vf = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black{sub_filter}"
        elif is_smart and (abs(cx - 0.5) > 0.02 or abs(cy - 0.5) > 0.02):
            x_expr = f"max(0,min(iw-ow,({cx:.4f}*iw)-(ow/2)))"
            # Keep face vertically centered or positioned naturally in top half
            y_expr = f"max(0,min(ih-oh,({cy:.4f}*ih)-(oh/3)))"
            vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}:x='{x_expr}':y='{y_expr}'{sub_filter}"
        else:
            # Standard center crop (safe fallback preserving exact output)
            vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}{sub_filter}"

    return vf

def cut(video, segs, c, i, vid, aspect_ratio="9:16", caption_style="beasty", framing="crop", out_dir=None, jid=None, export_profile=None, crf=None, preset=None, normalize_audio=None):
    profile = resolve_export_profile(export_profile)
    active_crf = str(crf if crf is not None else profile["crf"])
    active_preset = preset or profile.get("preset", "ultrafast")
    audio_bitrate = profile.get("audio_bitrate", "96k")
    should_normalize = normalize_audio if normalize_audio is not None else profile.get("normalize_audio", False)

    d = out_dir or os.path.join(OUT_DIR, str(vid))
    os.makedirs(d, exist_ok=True)
    srt_filename = f"clip{i}.srt"
    srt_path = os.path.join(d, srt_filename)

    has_captions = caption_style and caption_style.lower() not in ("none", "off", "false", "")
    if has_captions:
        make_srt(segs, c["start"], c["end"], srt_path, words=3)

    x_norm, y_norm = 0.5, 0.5
    if framing in ("smart", "face", "auto"):
        try:
            from reframe import detect_smart_crop_anchor
            x_norm, y_norm, detected = detect_smart_crop_anchor(video, c["start"], c["end"])
            if detected:
                print(f"[REFRAME] Clip {i}: Smart reframing focused at x={x_norm:.3f}, y={y_norm:.3f}")
            else:
                print(f"[REFRAME] Clip {i}: No face/speaker detected; safely using center crop.")
        except Exception as e:
            print(f"[REFRAME] Clip {i}: Smart reframing error ({e}); safely falling back to center crop.")
            x_norm, y_norm = 0.5, 0.5

    vf = get_filter_complex(
        aspect_ratio, caption_style, srt_filename,
        framing=framing, x_norm=x_norm, y_norm=y_norm,
        export_profile=profile["name"]
    )
    out = f"clip{i}.mp4"

    cmd = [
        "ffmpeg", "-y",
        "-threads", "2",
        "-ss", str(c["start"]),
        "-i", os.path.abspath(video),
        "-t", str(c["end"] - c["start"]),
        "-filter_complex", vf,
        "-c:v", "libx264",
        "-preset", active_preset,
        "-tune", "fastdecode",
        "-crf", active_crf,
    ]

    audio_filters = []
    if should_normalize:
        has_audio = check_has_audio(os.path.abspath(video))
        if has_audio:
            # EBU R128 integrated loudness normalization with -1.5 dB true peak limit
            audio_filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")

    if audio_filters:
        cmd.extend(["-af", ",".join(audio_filters)])

    cmd.extend([
        "-c:a", "aac",
        "-b:a", audio_bitrate,
        out,
    ])

    try:
        run_tracked_subprocess(cmd, jid=jid, cwd=d, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        err_msg = e.stderr.decode("utf-8", errors="replace")[-300:] if e.stderr else str(e)
        raise RuntimeError(f"FFmpeg failed for clip {i}: {err_msg}")
    finally:
        if has_captions and os.path.exists(srt_path):
            try:
                os.remove(srt_path)
            except Exception:
                pass

    with open(os.path.join(d, f"clip{i}.txt"), "w", encoding="utf-8") as f:
        score_line = f"Virality Score: {c.get('score', 92)}/100 • Category: {c.get('category', 'Viral Highlight')}\n"
        hook_line = f"Hook Intelligence: {c.get('hook_reason', 'Strong opening moment')}\n\n"
        f.write(f"{c['title']}\n\n{score_line}{hook_line}{c.get('caption', '')}\n\n{' '.join(c.get('hashtags', []))}\n")

    return os.path.join(d, out)
