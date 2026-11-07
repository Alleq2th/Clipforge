import os, subprocess
from config import OUT_DIR
from captions import make_srt

STYLE = "FontName=Arial,FontSize=14,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=3,Alignment=2,MarginV=300"
VF = ("[0:v]split[a][b];[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5[bg];"
      "[b]scale=1080:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,subtitles=clip.srt:force_style='{}'").format(STYLE)

def cut(video, segs, c, i, vid):
    d = os.path.join(OUT_DIR, vid); os.makedirs(d, exist_ok=True)
    make_srt(segs, c["start"], c["end"], os.path.join(d, "clip.srt"))
    out = f"clip{i}.mp4"
    subprocess.run(["ffmpeg", "-y", "-ss", str(c["start"]), "-i", os.path.abspath(video),
                    "-t", str(c["end"] - c["start"]), "-filter_complex", VF,
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", out],
                   cwd=d, check=True, capture_output=True)
    os.remove(os.path.join(d, "clip.srt"))
    open(os.path.join(d, f"clip{i}.txt"), "w", encoding="utf-8").write(
        f"{c['title']}\n\n{c['caption']}\n\n{' '.join(c['hashtags'])}\n")
    return os.path.join(d, out)
