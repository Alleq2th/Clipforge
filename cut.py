import os
import subprocess
from config import OUT_DIR
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

def get_filter_complex(aspect_ratio, caption_style, srt_filename="clip.srt", framing="crop", x_norm=0.5, y_norm=0.5):
    ratio = aspect_ratio if aspect_ratio in ("9:16", "1:1", "16:9") else "9:16"
    has_captions = caption_style and caption_style.lower() not in ("none", "off", "false", "")

    margin_v = 140 if ratio == "9:16" else (60 if ratio == "1:1" else 40)
    style_key = caption_style if caption_style in STYLES else "beasty"
    style_str = STYLES[style_key].format(margin_v=margin_v) if has_captions else ""
    sub_filter = f",subtitles={srt_filename}:force_style='{style_str}'" if has_captions else ""

    cx = max(0.05, min(0.95, float(x_norm)))
    cy = max(0.05, min(0.95, float(y_norm)))
    is_smart = framing in ("smart", "face", "auto")

    if ratio == "1:1":
        # 540x540 Square
        if framing == "blur":
            vf = (
                "[0:v]split[a][b];"
                "[a]scale=135:135:force_original_aspect_ratio=increase,crop=135:135,boxblur=4:1,scale=540:540[bg];"
                "[b]scale=540:540:force_original_aspect_ratio=decrease,pad=540:540:(ow-iw)/2:(oh-ih)/2:color=black@0[fg];"
                f"[bg][fg]overlay=0:0{sub_filter}"
            )
        elif is_smart and (abs(cx - 0.5) > 0.02 or abs(cy - 0.5) > 0.02):
            x_expr = f"max(0,min(iw-ow,({cx:.4f}*iw)-(ow/2)))"
            y_expr = f"max(0,min(ih-oh,({cy:.4f}*ih)-(oh/2)))"
            vf = f"scale=540:540:force_original_aspect_ratio=increase,crop=540:540:x='{x_expr}':y='{y_expr}'{sub_filter}"
        else:
            vf = f"scale=540:540:force_original_aspect_ratio=increase,crop=540:540{sub_filter}"

    elif ratio == "16:9":
        # 960x540 Landscape
        vf = f"scale=960:540:force_original_aspect_ratio=decrease,pad=960:540:(ow-iw)/2:(oh-ih)/2:color=black{sub_filter}"

    else:
        # 9:16 Vertical (540x960)
        if framing == "blur":
            vf = (
                "[0:v]split[a][b];"
                "[a]scale=135:240:force_original_aspect_ratio=increase,crop=135:240,boxblur=4:1,scale=540:960[bg];"
                "[b]scale=540:960:force_original_aspect_ratio=decrease,pad=540:960:(ow-iw)/2:(oh-ih)/2:color=black@0[fg];"
                f"[bg][fg]overlay=0:0{sub_filter}"
            )
        elif framing == "fit":
            vf = f"scale=540:960:force_original_aspect_ratio=decrease,pad=540:960:(ow-iw)/2:(oh-ih)/2:color=black{sub_filter}"
        elif is_smart and (abs(cx - 0.5) > 0.02 or abs(cy - 0.5) > 0.02):
            x_expr = f"max(0,min(iw-ow,({cx:.4f}*iw)-(ow/2)))"
            # Keep face vertically centered or positioned naturally in top half
            y_expr = f"max(0,min(ih-oh,({cy:.4f}*ih)-(oh/3)))"
            vf = f"scale=540:960:force_original_aspect_ratio=increase,crop=540:960:x='{x_expr}':y='{y_expr}'{sub_filter}"
        else:
            # Standard center crop (safe fallback preserving exact current output)
            vf = f"scale=540:960:force_original_aspect_ratio=increase,crop=540:960{sub_filter}"

    return vf

def cut(video, segs, c, i, vid, aspect_ratio="9:16", caption_style="beasty", framing="crop", out_dir=None, jid=None):
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

    vf = get_filter_complex(aspect_ratio, caption_style, srt_filename, framing=framing, x_norm=x_norm, y_norm=y_norm)
    out = f"clip{i}.mp4"

    cmd = [
        "ffmpeg", "-y",
        "-threads", "2",
        "-ss", str(c["start"]),
        "-i", os.path.abspath(video),
        "-t", str(c["end"] - c["start"]),
        "-filter_complex", vf,
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "fastdecode",
        "-crf", "26",
        "-c:a", "aac",
        "-b:a", "96k",
        out,
    ]
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
