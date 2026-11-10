import os
import sys
import time
import uuid
import asyncio
import subprocess
import re
from config import WORK_DIR, OUT_DIR
from voice_profiles import get_voice_profile
from security import validate_video_url, safe_join_path
from process_manager import run_tracked_subprocess
from storage import get_job_work_dir, get_job_out_dir, cleanup_job_work_dir, get_storage_provider

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

async def _synthesize_edge_tts(text, voice, out_mp3, out_srt, rate="+0%", pitch="+0Hz"):
    import edge_tts
    communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
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
    rate = "+0%"
    pitch = "+0Hz"
    voice = VOICES.get("deep_narrator")

    if str(voice_key).startswith("vp_"):
        vp = get_voice_profile(voice_key)
        if vp:
            voice = vp.get("base_voice") or voice
            rate = vp.get("rate") or "+0%"
            pitch = vp.get("pitch") or "+0Hz"
    elif voice_key in VOICES:
        voice = VOICES[voice_key]

    try:
        asyncio.run(_synthesize_edge_tts(text, voice, out_mp3, out_srt, rate=rate, pitch=pitch))
        if os.path.exists(out_mp3) and os.path.getsize(out_mp3) > 1000:
            return True
    except Exception as e:
        print(f"[FACELESS] edge-tts synthesis notice: {e}")

    # Fallback to generated audio tone if edge-tts is temporarily offline
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

def create_faceless_video(video_source, script_text, voice_key="deep_narrator", caption_style="beasty", framing="crop", fid=None, cb=print, check_cancelled=None):
    fid = fid or str(uuid.uuid4())[:8]
    work_dir = get_job_work_dir(f"faceless_{fid}")
    out_dir = safe_join_path(OUT_DIR, "faceless", str(fid))
    os.makedirs(out_dir, exist_ok=True)

    cb("Synthesizing neural voiceover...")
    voice_mp3 = os.path.join(work_dir, "voice.mp3")
    voice_srt = os.path.join(work_dir, "sub.srt")
    synthesize_speech(script_text, voice_key, voice_mp3, voice_srt)

    duration = get_audio_duration(voice_mp3)
    cb(f"Voiceover ready ({round(duration, 1)}s) • Trimming gameplay footage...")

    if check_cancelled and check_cancelled():
        cleanup_job_work_dir(f"faceless_{fid}")
        cb("Cancelled by user")
        return None

    bg_video = None
    downloaded_source = None
    if video_source and (video_source.startswith("http://") or video_source.startswith("https://")):
        from download import download
        validate_video_url(video_source)
        cb(f"Downloading gameplay section (first {int(duration + 5)}s)...")
        bg_video, _ = download(video_source, dest_dir=work_dir, max_duration=duration + 5.0, check_cancelled=check_cancelled)
        downloaded_source = bg_video
    elif video_source and os.path.exists(video_source):
        bg_video = video_source
    else:
        bg_video = os.path.join(work_dir, "gta_bg.mp4")
        run_tracked_subprocess([
            "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=24",
            "-t", "5", "-c:v", "libx264", "-preset", "ultrafast", bg_video
        ], jid=fid, check=True)

    if check_cancelled and check_cancelled():
        cleanup_job_work_dir(f"faceless_{fid}")
        cb("Cancelled by user")
        return None

    # Immediate fast stream copy trim
    trimmed_bg = os.path.join(work_dir, "trimmed_bg.mp4")
    try:
        run_tracked_subprocess([
            "ffmpeg", "-y", "-ss", "0", "-t", str(duration + 1.0),
            "-i", os.path.abspath(bg_video),
            "-c", "copy", "-avoid_negative_ts", "make_zero", trimmed_bg
        ], jid=fid, check=True)
        if os.path.exists(trimmed_bg) and os.path.getsize(trimmed_bg) > 1000:
            bg_video = trimmed_bg
    except Exception as e:
        print(f"[FACELESS] Fast trim warning: {e}")

    cb("Processing gameplay, stripping original audio & burning captions...")
    has_captions = caption_style and caption_style.lower() not in ("none", "off", "false", "")
    style_str = STYLES.get(caption_style, STYLES["beasty"])

    sub_srt_in_out = os.path.join(out_dir, "sub.srt")
    if os.path.exists(voice_srt):
        import shutil
        shutil.copy(voice_srt, sub_srt_in_out)

    sub_filter = f",subtitles=sub.srt:force_style='{style_str}'" if has_captions else ""

    if framing == "blur":
        vf = (
            "[0:v]split[a][b];"
            "[a]scale=135:240:force_original_aspect_ratio=increase,crop=135:240,boxblur=4:1,scale=540:960[bg];"
            "[b]scale=540:960:force_original_aspect_ratio=decrease,pad=540:960:(ow-iw)/2:(oh-ih)/2:color=black@0[fg];"
            f"[bg][fg]overlay=0:0{sub_filter}"
        )
    elif framing == "fit":
        vf = f"scale=540:960:force_original_aspect_ratio=decrease,pad=540:960:(ow-iw)/2:(oh-ih)/2:color=black{sub_filter}"
    else:
        vf = f"scale=540:960:force_original_aspect_ratio=increase,crop=540:960{sub_filter}"

    out_filename = "gta6_clip.mp4"
    final_out = os.path.join(out_dir, out_filename)

    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1",
        "-i", os.path.abspath(bg_video),
        "-i", os.path.abspath(voice_mp3),
        "-filter_complex", vf,
        "-map", "[bg]" if framing == "blur" and not has_captions else "0:v",
        "-map", "1:a:0",
        "-t", str(duration),
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "fastdecode",
        "-crf", "26",
        "-c:a", "aac",
        "-b:a", "128k",
        "-shortest",
        out_filename
    ]
    # Adjust mapping if filter_complex outputs a final stream
    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1",
        "-i", os.path.abspath(bg_video),
        "-i", os.path.abspath(voice_mp3),
        "-filter_complex", f"[0:v]{vf}[v_out]",
        "-map", "[v_out]",
        "-map", "1:a:0",
        "-t", str(duration),
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "fastdecode",
        "-crf", "26",
        "-c:a", "aac",
        "-b:a", "128k",
        "-shortest",
        out_filename
    ]

    try:
        run_tracked_subprocess(cmd, jid=fid, cwd=out_dir, check=True)
    except subprocess.CalledProcessError as e:
        err_msg = e.stderr.decode("utf-8", errors="replace")[-300:] if e.stderr else str(e)
        raise RuntimeError(f"FFmpeg failed in faceless video: {err_msg}")
    finally:
        if os.path.exists(sub_srt_in_out):
            try: os.remove(sub_srt_in_out)
            except Exception: pass
        cleanup_job_work_dir(f"faceless_{fid}")

    provider = get_storage_provider()
    rel_dest = f"faceless/{fid}/{out_filename}"
    cloud_url = provider.save_clip(final_out, rel_dest)
    file_url = cloud_url if cloud_url else f"/clips/faceless/{fid}/{out_filename}"

    cb("Faceless video ready!")
    return {
        "filename": out_filename,
        "path": final_out,
        "duration": round(duration, 1),
        "file_url": file_url,
        "cloud_url": cloud_url or ""
    }
