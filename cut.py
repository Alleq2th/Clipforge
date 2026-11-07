import os, subprocess
from config import OUT_DIR
from captions import make_srt

STYLE = "FontName=Arial,FontSize=13,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2.5,Alignment=2,MarginV=180"

# Optimized for low-memory (512MB RAM) and high speed:
# Scales background to 360x640 for lightning-fast blur, then scales to 720x1280 vertical HD.
# This cuts memory consumption by ~85% and prevents OOM crashes on free hosting tiers.
VF = (
    "[0:v]split[a][b];"
    "[a]scale=360:640:force_original_aspect_ratio=increase,crop=360:640,boxblur=10:2,scale=720:1280[bg];"
    "[b]scale=720:-2[fg];"
    "[bg][fg]overlay=(W-w)/2:(H-h)/2,subtitles=clip.srt:force_style='{}'"
).format(STYLE)

def cut(video, segs, c, i, vid):
    d = os.path.join(OUT_DIR, vid)
    os.makedirs(d, exist_ok=True)
    srt_path = os.path.join(d, "clip.srt")
    make_srt(segs, c["start"], c["end"], srt_path)
    out = f"clip{i}.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-threads", "2",
        "-ss", str(c["start"]),
        "-i", os.path.abspath(video),
        "-t", str(c["end"] - c["start"]),
        "-filter_complex", VF,
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "24",
        "-c:a", "aac",
        "-b:a", "128k",
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
        f.write(f"{c['title']}\n\n{c['caption']}\n\n{' '.join(c['hashtags'])}\n")
    return os.path.join(d, out)
