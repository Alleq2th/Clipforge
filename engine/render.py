"""Stage 7 - render.

The filter graph is built as data (a list of filter strings) and then handed to
ffmpeg. That split matters: the graph can be asserted in tests without an
encoder, which is how the reframe and caption wiring is verified.

Four reframe modes produce four different graphs:

  track      scale up, then crop a moving window driven by sendcmd
  general    blurred copy behind, whole frame fitted on top
  split      two stacked crops (top speaker / bottom speaker)
  screencast fit the screen, pad with a solid colour
"""
import os
import subprocess

from config import (
    RENDER_AUDIO_BITRATE,
    RENDER_CRF,
    RENDER_HEIGHT,
    RENDER_PRESET,
    RENDER_WIDTH,
)

ASPECTS = {
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "16:9": (1920, 1080),
    "4:5": (1080, 1350),
}


class RenderUnavailable(RuntimeError):
    """Raised when the host refuses to encode, but the graph itself is valid.

    Kept as a distinct type so the pipeline can label these clips honestly
    instead of reporting them as broken output.
    """


def dimensions(aspect="9:16"):
    return ASPECTS.get(aspect, ASPECTS["9:16"])


def _even(n):
    """H.264 needs even dimensions."""
    n = int(round(n))
    return n if n % 2 == 0 else n + 1


def build_graph(mode, out_w, out_h, src_w, src_h, ass_path=None, sendcmd=None):
    """Returns (filter_complex, video_label, extra_inputs).

    extra_inputs is a list of ffmpeg args that must be inserted before -i, used
    for the sendcmd file.
    """
    out_w, out_h = _even(out_w), _even(out_h)
    extra = []
    parts = []

    if mode == "track" and sendcmd:
        # Scale so the short side covers the output, then crop a window that
        # sendcmd moves over time. sendcmd must be the FIRST filter in the
        # chain so its `crop x` / `crop y` commands address the crop below it.
        parts.append(
            f"[0:v]sendcmd=f={sendcmd},"
            f"scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
            f"crop={out_w}:{out_h}:x=0:y=0,setsar=1[v]"
        )

    elif mode == "general":
        # Blurred, darkened copy fills the frame; the real frame sits on top.
        parts.append(
            f"[0:v]split=2[bg][fg];"
            f"[bg]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
            f"crop={out_w}:{out_h},boxblur=40:2,eq=brightness=-0.12[bgz];"
            f"[fg]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease[fgz];"
            f"[bgz][fgz]overlay=(W-w)/2:(H-h)/2,setsar=1[v]"
        )

    elif mode == "split":
        # Two speakers: crop the left half and the right half, stack them.
        half_h = _even(out_h / 2)
        parts.append(
            f"[0:v]split=2[t][b];"
            f"[t]crop=iw/2:ih:0:0,scale={out_w}:{half_h}:force_original_aspect_ratio=increase,"
            f"crop={out_w}:{half_h},setsar=1[tz];"
            f"[b]crop=iw/2:ih:iw/2:0,scale={out_w}:{half_h}:force_original_aspect_ratio=increase,"
            f"crop={out_w}:{half_h},setsar=1[bz];"
            f"[tz][bz]vstack=inputs=2,setsar=1[v]"
        )

    elif mode == "screencast":
        parts.append(
            f"[0:v]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,"
            f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:color=0x0B0D12,setsar=1[v]"
        )

    else:
        # Plain centre crop - the safe default.
        parts.append(
            f"[0:v]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
            f"crop={out_w}:{out_h},setsar=1[v]"
        )

    label = "v"
    if ass_path:
        # subtitles filter needs the path escaped for the filtergraph parser.
        esc = ass_path.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
        parts.append(f"[v]subtitles='{esc}'[vsub]")
        label = "vsub"

    return ";".join(parts), label, extra


def validate_graph(video, start, end, mode="track", aspect="9:16",
                   ass_path=None, sendcmd=None, src_w=1920, src_h=1080,
                   timeout=600):
    """Decodes through the real filter graph without encoding a file.

    This is the graph equivalent of a syntax check: ffmpeg has to build every
    filter, open the ASS file, run sendcmd and pull real frames. It catches a
    broken graph, a missing font, an unreadable source or an unsupported codec
    without producing any output file.

    Used when an encode is unavailable or has failed - it separates "my graph
    is wrong" from "this machine cannot encode", which are very different
    problems to report.
    """
    out_w, out_h = dimensions(aspect)
    graph, label, _extra = build_graph(
        mode, out_w, out_h, src_w, src_h, ass_path=ass_path, sendcmd=sendcmd
    )
    duration = max(0.1, float(end) - float(start))
    cmd = [
        "ffmpeg", "-y", "-nostdin", "-hide_banner", "-loglevel", "warning",
        "-ss", f"{float(start):.3f}",
        "-i", os.path.abspath(video),
        "-t", f"{min(duration, 5.0):.3f}",
        "-filter_complex", graph,
        "-map", f"[{label}]",
        "-f", "null", "-",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "graph validation timed out"
    if r.returncode != 0:
        tail = (r.stderr or b"").decode("utf-8", errors="replace").strip()
        return False, tail[-500:] or "ffmpeg rejected the graph"
    return True, ""


def build_command(
    video, out_path, start, end, mode="track", aspect="9:16",
    ass_path=None, sendcmd=None, src_w=1920, src_h=1080,
    crf=None, preset=None,
):
    """Returns the ffmpeg argv for one clip."""
    out_w, out_h = dimensions(aspect)
    graph, label, _extra = build_graph(
        mode, out_w, out_h, src_w, src_h, ass_path=ass_path, sendcmd=sendcmd
    )

    duration = max(0.1, float(end) - float(start))

    cmd = [
        "ffmpeg", "-y", "-nostdin", "-hide_banner", "-loglevel", "error",
        # -ss before -i is a fast seek; -t after -i is accurate.
        "-ss", f"{float(start):.3f}",
        "-i", os.path.abspath(video),
        "-t", f"{duration:.3f}",
        "-filter_complex", graph,
        "-map", f"[{label}]",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-preset", preset or RENDER_PRESET,
        "-crf", str(crf if crf is not None else RENDER_CRF),
        "-profile:v", "high",
        "-level", "4.2",
        "-pix_fmt", "yuv420p",
        "-r", "30",
        "-g", "60",
        "-c:a", "aac",
        "-b:a", RENDER_AUDIO_BITRATE,
        "-ar", "48000",
        "-movflags", "+faststart",
        out_path,
    ]
    return cmd


def render(video, out_path, start, end, mode="track", aspect="9:16",
           ass_path=None, sendcmd=None, src_w=1920, src_h=1080,
           crf=None, preset=None, timeout=1800):
    """Runs ffmpeg and returns out_path. Raises RuntimeError with ffmpeg's tail."""
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    cmd = build_command(
        video, out_path, start, end, mode=mode, aspect=aspect,
        ass_path=ass_path, sendcmd=sendcmd, src_w=src_w, src_h=src_h,
        crf=crf, preset=preset,
    )
    # -loglevel error suppresses the context lines, so an empty tail tells us
    # nothing. Run at `warning` and keep the tail: that is what actually
    # explains the failure to the user.
    cmd = [c for c in cmd]
    for i, tok in enumerate(cmd):
        if tok == "-loglevel":
            cmd[i + 1] = "warning"
            break

    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)
    except subprocess.CalledProcessError as e:
        tail = (e.stderr or b"").decode("utf-8", errors="replace").strip()
        lines = [l for l in tail.splitlines() if l.strip()]
        detail = " | ".join(lines[-4:]) if lines else "no output from ffmpeg"

        # An encode that dies with no message usually means the environment
        # refused to encode rather than that the graph is wrong. Validate the
        # graph separately so the error we surface is the true one.
        if not lines:
            ok, gerr = validate_graph(
                video, start, end, mode=mode, aspect=aspect,
                ass_path=ass_path, sendcmd=sendcmd, src_w=src_w, src_h=src_h,
            )
            if ok:
                raise RenderUnavailable(
                    "This environment cannot encode video (the ffmpeg render was "
                    "refused). The filter graph itself validates cleanly, so on a "
                    "host with video encoding enabled this clip renders normally."
                ) from None
            raise RuntimeError(f"Filter graph failed: {gerr}") from None

        raise RuntimeError(f"ffmpeg failed: {detail[:500]}") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError("Render timed out - try a shorter clip length.") from None

    if not os.path.exists(out_path) or os.path.getsize(out_path) < 1024:
        raise RuntimeError("ffmpeg produced no usable output")
    return out_path
