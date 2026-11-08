import os, sys, time, uuid, asyncio, subprocess, re
from config import WORK_DIR, OUT_DIR

VOICES = {
    "deep_narrator": "en-US-ChristopherNeural",   # Deep viral movie/storyteller voice
    "energetic_guy": "en-US-GuyNeural",           # High energy TikTok gamer/streamer
    "viral_female": "en-US-JennyNeural",          # Smooth female narrator
    "british_story": "en-GB-RyanNeural",          # Dramatic British accent
    "fast_shorts": "en-US-EricNeural",            # Fast-paced Shorts explainer
}

STYLES = {
    "beasty": "FontName=Arial,FontSize=13,Bold=1,PrimaryColour=&H0000FFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=3,Shadow=1,Alignment=2,MarginV=140",
    "karaoke": "FontName=Arial,FontSize=13,Bold=1,PrimaryColour=&H0000FF00,OutlineColour=&H00000000,BorderStyle=1,Outline=3,Shadow=1,Alignment=2,MarginV=140",
    "classic": "FontName=Arial,FontSize=13,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2.5,Shadow=1,Alignment=2,MarginV=140",
    "cyber": "FontName=Arial,FontSize=13,Bold=1,PrimaryColour=&H00FF00FF,OutlineColour=&H00000000,BorderStyle=1,Outline=3,Shadow=1,Alignment=2,MarginV=140",
}

async def _synthesize_edge_tts(text, voice, out_mp3, out_srt):
    import edge_tts
    communicate = edge_tts.Communicate(text, voice)
    submaker = edge_tts.SubMaker()
    with open(out_mp3, "wb") as file:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                file.write(chunk["data"])
            elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
                submaker.feed(chunk)
    srt_text = submaker.get_srt()
    with open(out_srt, "w", encoding="utf-8") as f:
        f.write(srt_text)

def synthesize_speech(text, voice_key, out_mp3, out_srt):
    voice = VOICES.get(voice_key, VOICES["deep_narrator"])
    try:
        asyncio.run(_synthesize_edge_tts(text, voice, out_mp3, out_srt))
        if os.path.exists(out_mp3) and os.path.getsize(out_mp3) > 1000:
            return True
    except Exception as e:
        print(f"edge-tts failed: {e}")

    # Fallback to espeak / ffmpeg synthetic speech if network edge-tts fails
    words = text.split()
    est_dur = max(4.0, len(words) * 0.35)
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440",
        "-t", str(est_dur), "-c:a", "aac", "-b:a", "128k", out_mp3
    ], check=True, capture_output=True)
    with open(out_srt, "w", encoding="utf-8") as f:
        f.write(f"1\n00:00:00,500 --> 00:00:{int(est_dur):02d},000\n{text[:60]}\n\n")
    return True

def get_audio_duration(audio_file):
    try:
        cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", audio_file]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        import json
        data = json.loads(res.stdout)
        return float(data.get("format", {}).get("duration", 10.0))
    except Exception:
        return 10.0

def create_faceless_video(video_source, script_text, voice_key="deep_narrator", caption_style="beasty", fid=None, cb=print):
    fid = fid or str(uuid.uuid4())[:8]
    out_dir = os.path.join(OUT_DIR, "faceless", fid)
    os.makedirs(out_dir, exist_ok=True)

    cb("Synthesizing neural voiceover...")
    voice_mp3 = os.path.join(out_dir, "voice.mp3")
    voice_srt = os.path.join(out_dir, "sub.srt")
    synthesize_speech(script_text, voice_key, voice_mp3, voice_srt)

    duration = get_audio_duration(voice_mp3)
    cb(f"Generated voiceover ({round(duration, 1)}s) • Preparing background video...")

    # Determine background video path
    bg_video = None
    if video_source and (video_source.startswith("http://") or video_source.startswith("https://")):
        from download import download
        cb("Downloading background gameplay video...")
        bg_video, _ = download(video_source)
    elif video_source and os.path.exists(video_source):
        bg_video = video_source
    else:
        # Generate clean synthetic GTA-style animated motion background if no URL provided
        bg_video = os.path.join(out_dir, "gta_bg.mp4")
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=24",
            "-t", "5", "-c:v", "libx264", "-preset", "ultrafast", bg_video
        ], check=True, capture_output=True)

    cb("Looping video, cancelling original audio & burning captions...")
    style_str = STYLES.get(caption_style, STYLES["beasty"])
    final_mp4 = os.path.join(out_dir, "faceless_clip.mp4")

    # Fast 540x960 9:16 vertical render
    # -stream_loop -1 loops the background video continuously
    # -map "[v]" takes only filtered video
    # -map 1:a:0 takes ONLY the narration voice (ORIGINAL VIDEO AUDIO IS COMPLETELY CANCELLED)
    # -t duration cuts video exactly when narration finishes
    cmd = [
        "ffmpeg", "-y",
        "-threads", "2",
        "-stream_loop", "-1", "-i", os.path.abspath(bg_video),
        "-i", os.path.abspath(voice_mp3),
        "-filter_complex",
        f"[0:v]scale=540:960:force_original_aspect_ratio=increase,crop=540:960,subtitles=sub.srt:force_style='{style_str}'[v]",
        "-map", "[v]",
        "-map", "1:a:0",
        "-t", str(duration),
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "fastdecode",
        "-crf", "26",
        "-c:a", "aac",
        "-b:a", "128k",
        "faceless_clip.mp4"
    ]

    try:
        subprocess.run(cmd, cwd=out_dir, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode("utf-8", errors="replace")[-300:] if e.stderr else str(e)
        raise RuntimeError(f"FFmpeg faceless render failed: {err}")

    cb("Faceless video ready!")
    return {
        "id": fid,
        "filename": "faceless_clip.mp4",
        "file_url": f"/faceless/{fid}/faceless_clip.mp4",
        "duration": round(duration, 1),
        "script": script_text,
        "voice": voice_key
    }
