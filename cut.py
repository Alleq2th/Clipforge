import os, subprocess
from config import OUT_DIR
from captions import make_srt

# Subtitle styles matching OpusClip / Vizard presets (ASS format BGR hex: &H00BBGGRR)
STYLES = {
    "beasty": "FontName=Arial,FontSize=13,Bold=1,PrimaryColour=&H0000FFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=3,Shadow=1,Alignment=2,MarginV={margin_v}",
    "karaoke": "FontName=Arial,FontSize=13,Bold=1,PrimaryColour=&H0000FF00,OutlineColour=&H00000000,BorderStyle=1,Outline=3,Shadow=1,Alignment=2,MarginV={margin_v}",
    "classic": "FontName=Arial,FontSize=13,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2.5,Shadow=1,Alignment=2,MarginV={margin_v}",
    "cyber": "FontName=Arial,FontSize=13,Bold=1,PrimaryColour=&H00FF00FF,OutlineColour=&H00000000,BorderStyle=1,Outline=3,Shadow=1,Alignment=2,MarginV={margin_v}",
}

def get_filter_complex(aspect_ratio, caption_style, srt_filename="clip.srt"):
    ratio = aspect_ratio if aspect_ratio in ("9:16", "1:1", "16:9") else "9:16"
    style_key = caption_style if caption_style in STYLES else "beasty"

    # MarginV: safe positioning above TikTok / Shorts navigation UI
    margin_v = 140 if ratio == "9:16" else (60 if ratio == "1:1" else 40)
    style_str = STYLES[style_key].format(margin_v=margin_v)

    if ratio == "1:1":
        # 720x720 Square with blurred backdrop
        vf = (
            "[0:v]split[a][b];"
            "[a]scale=360:360:force_original_aspect_ratio=increase,crop=360:360,boxblur=10:2,scale=720:720[bg];"
            "[b]scale=720:-2[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,subtitles={srt_filename}:force_style='{style_str}'"
        )
    elif ratio == "16:9":
        # 1280x720 Widescreen landscape with burned-in subtitles
        vf = (
            f"scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,"
            f"subtitles={srt_filename}:force_style='{style_str}'"
        )
    else:
        # 9:16 Vertical HD (720x1280) with ultra-fast blurred background
        vf = (
            "[0:v]split[a][b];"
            "[a]scale=360:640:force_original_aspect_ratio=increase,crop=360:640,boxblur=10:2,scale=720:1280[bg];"
            "[b]scale=720:-2[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,subtitles={srt_filename}:force_style='{style_str}'"
        )
    return vf

def cut(video, segs, c, i, vid, aspect_ratio="9:16", caption_style="beasty"):
    d = os.path.join(OUT_DIR, vid)
    os.makedirs(d, exist_ok=True)
    srt_path = os.path.join(d, "clip.srt")
    make_srt(segs, c["start"], c["end"], srt_path, words=3)

    vf = get_filter_complex(aspect_ratio, caption_style, "clip.srt")
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
        "-crf", "26",
        "-c:a", "aac",
        "-b:a", "96k",
        out,
    ]
    try:
        subprocess.run(cmd, cwd=d, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        err_msg = e.stderr.decode("utf-8", errors="replace")[-300:] if e.stderr else str(e)
        raise RuntimeError(f"FFmpeg failed for clip {i}: {err_msg}")
    finally:
        if os.path.exists(srt_path):
            try:
                os.remove(srt_path)
            except Exception:
                pass

    with open(os.path.join(d, f"clip{i}.txt"), "w", encoding="utf-8") as f:
        score_line = f"Virality Score: {c.get('score', 92)}/100 • Category: {c.get('category', 'Viral Highlight')}\n"
        hook_line = f"Hook Intelligence: {c.get('hook_reason', 'Strong opening moment')}\n\n"
        f.write(f"{c['title']}\n\n{score_line}{hook_line}{c['caption']}\n\n{' '.join(c.get('hashtags', []))}\n")
    return os.path.join(d, out)
