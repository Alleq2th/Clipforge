"""Stage 5 - auto-reframe (SmartReframe).

Four named modes, because naive auto-reframe breaks in four different ways:

  TRACK      one face, follow it. The default for talking heads and podcasts.
  GENERAL    no reliable subject. Blur-fill the background, keep the whole frame.
  SPLIT      two people. Stack them: speaker on top, other speaker below.
  SCREENCAST screen recording. Never crop the content - fit it and pad.

TRACK is the interesting one. It samples the clip at REFRAME_SAMPLE_FPS, runs
MediaPipe BlazeFace on each sample, and builds a crop path. The path is then
smoothed with an exponential filter and emitted as an ffmpeg `sendcmd` script so
the crop moves continuously instead of jumping between detections.

Everything here is pure computation over a list of detections, so the path
logic is unit-testable without touching a video encoder.
"""
import os
import subprocess

from config import REFRAME_HEADROOM, REFRAME_SAMPLE_FPS, REFRAME_SMOOTHING

MODES = ("track", "general", "split", "screencast")

MODE_LABELS = {
    "track": "TRACK - follow the speaker",
    "general": "GENERAL - blur-fill, keep the frame",
    "split": "SPLIT - two speakers, stacked",
    "screencast": "SCREENCAST - fit the screen, never crop",
}

_MODEL_CANDIDATES = (
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "models", "blaze_face_short_range.tflite"),
    "/app/models/blaze_face_short_range.tflite",
    os.path.join(os.getcwd(), "models", "blaze_face_short_range.tflite"),
)


def _model_path():
    for p in _MODEL_CANDIDATES:
        if os.path.isfile(p) and os.path.getsize(p) > 1000:
            return p
    return None


def _detector():
    """Returns a MediaPipe FaceDetector, or None if unavailable."""
    path = _model_path()
    if not path:
        return None
    try:
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision

        base = mp_python.BaseOptions(model_asset_path=path)
        opts = vision.FaceDetectorOptions(base_options=base, min_detection_confidence=0.4)
        return vision.FaceDetector.create_from_options(opts)
    except Exception as e:  # noqa: BLE001
        print(f"[REFRAME] MediaPipe unavailable ({e}); falling back to OpenCV.")
        return None


def _detect_mediapipe(detector, frame_rgb):
    """Returns [(cx, cy, w, h, score)] normalised 0-1 for each face in the frame."""
    import mediapipe as mp

    h, w = frame_rgb.shape[:2]
    image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
    result = detector.detect(image)
    out = []
    for det in result.detections or []:
        box = det.bounding_box
        score = 0.0
        try:
            score = float(det.categories[0].score)
        except (AttributeError, IndexError, TypeError):
            score = 0.5
        out.append((
            (box.origin_x + box.width / 2.0) / w,
            (box.origin_y + box.height / 2.0) / h,
            box.width / w,
            box.height / h,
            score,
        ))
    return out


def _detect_opencv(cascade, frame_gray):
    """Haar fallback. Less accurate than BlazeFace but needs no model file."""
    h, w = frame_gray.shape[:2]
    faces = cascade.detectMultiScale(frame_gray, scaleFactor=1.1, minNeighbors=4, minSize=(30, 30))
    return [
        ((x + fw / 2.0) / w, (y + fh / 2.0) / h, fw / w, fh / h, 0.5)
        for (x, y, fw, fh) in faces
    ]


def _face_weight(face):
    """Ranks a detection as a likely real subject.

    BlazeFace fires on lamps, doorways and high-contrast blobs. Three signals
    separate a real face from a false positive:

      * confidence  - the model's own score, the strongest signal
      * area        - a real subject is usually a decent chunk of the frame
      * position    - faces sit in the middle band, not jammed against the top
                      edge (where lights and signage live)

    Weighting confidence highest is what stops a big bright lamp from winning
    over the actual speaker.
    """
    cx, cy, w, h, score = face
    area = w * h
    # Penalise detections hugging the top edge; they are almost always objects.
    top_penalty = 0.35 if cy < 0.16 else 1.0
    # Penalise tiny detections - background faces are not the subject.
    size_factor = min(1.0, area / 0.02)
    return (score ** 2) * (area ** 0.5) * top_penalty * size_factor


def sample_detections(video, start, end, sample_fps=None):
    """Samples the clip and returns [(t, [(cx, cy, w, h), ...])] in clip-local time.

    t is seconds from the start of the clip, so the crop path lines up with the
    rendered output rather than the source timeline.
    """
    sample_fps = sample_fps or REFRAME_SAMPLE_FPS
    duration = max(0.1, float(end) - float(start))
    n = max(3, min(400, int(duration * sample_fps)))
    times = [start + duration * (i + 0.5) / n for i in range(n)]

    try:
        import cv2
        import numpy as np
    except ImportError:
        return []

    detector = _detector()
    cascade = None
    if detector is None:
        try:
            cascade_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "models", "haarcascade_frontalface_default.xml",
            )
            if os.path.isfile(cascade_path):
                cascade = cv2.CascadeClassifier(cascade_path)
                if cascade.empty():
                    cascade = None
        except Exception:  # noqa: BLE001
            cascade = None

    if detector is None and cascade is None:
        return []

    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        return []

    out = []
    try:
        for t in times:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue

            # Downscale for speed; detection accuracy is unaffected at 480px.
            h, w = frame.shape[:2]
            if w > 480:
                scale = 480.0 / w
                frame = cv2.resize(frame, (480, int(h * scale)), interpolation=cv2.INTER_AREA)

            if detector is not None:
                faces = _detect_mediapipe(detector, cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            else:
                faces = _detect_opencv(cascade, cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))

            out.append((round(t - start, 3), faces))
    finally:
        cap.release()

    return out


def build_crop_path(samples, smoothing=None, headroom=None):
    """Turns raw detections into a smooth, monotonic crop path.

    Args:
        samples: [(t, [(cx, cy, w, h), ...])] in clip-local seconds.
        smoothing: 0-1 exponential factor. Lower = smoother, more lag.
        headroom: fraction of frame height the subject's head sits below the top.

    Returns:
        [(t, x_norm, y_norm)] where x/y are the desired crop centre, clamped to
        [0,1]. Empty list means "no subject found, use centre crop".
    """
    smoothing = REFRAME_SMOOTHING if smoothing is None else smoothing
    headroom = REFRAME_HEADROOM if headroom is None else headroom

    # --- pick the primary subject per sample: the highest-weighted face, but
    # prefer the one closest to the previous position so we do not flip between
    # speakers mid-clip.
    picked = []
    prev = None
    for t, faces in samples:
        if not faces:
            picked.append((t, None))
            continue
        if prev is None:
            best = max(faces, key=_face_weight)
        else:
            best = max(
                faces,
                key=lambda f: _face_weight(f) * (1.0 - min(0.6, abs(f[0] - prev[0]))),
            )
        prev = best
        picked.append((t, best))

    if not any(f for _, f in picked):
        return []

    # --- forward pass: exponential smoothing over the detected positions.
    # Gaps (no detection) hold the last known position rather than snapping to
    # centre, which is what causes the visible "jump" in naive implementations.
    path = []
    sx = sy = None
    for t, face in picked:
        if face is not None:
            cx, cy = face[0], face[1]
            if sx is None:
                sx, sy = cx, cy
            else:
                sx += smoothing * (cx - sx)
                sy += smoothing * (cy - sy)
        if sx is None:
            continue
        path.append((t, sx, sy))

    if not path:
        return []

    # --- backward pass: removes the lag introduced by the forward filter, so
    # the crop is centred on the subject at the moment they are on screen.
    bx = by = None
    smoothed = []
    for t, x, y in reversed(path):
        if bx is None:
            bx, by = x, y
        else:
            bx += smoothing * (x - bx)
            by += smoothing * (y - by)
        smoothed.append((t, bx, by))
    smoothed.reverse()

    # --- apply headroom: the crop centre sits below the face centre so the
    # subject's head is not jammed against the top edge.
    out = []
    for t, x, y in smoothed:
        out.append((
            round(t, 3),
            round(max(0.0, min(1.0, x)), 4),
            round(max(0.0, min(1.0, y + headroom * 0.5)), 4),
        ))
    return out


def write_sendcmd(path, crop_path, out_w, out_h, src_w, src_h):
    """Writes an ffmpeg sendcmd script that moves the crop window over time.

    The crop x/y are expressed in source pixels. We clamp so the window never
    leaves the frame, which is what produces the hard edge in bad reframes.
    """
    max_x = max(0, src_w - out_w)
    max_y = max(0, src_h - out_h)

    lines = []
    for t, x_norm, y_norm in crop_path:
        x = int(round(max(0.0, min(max_x, x_norm * src_w - out_w / 2.0))))
        y = int(round(max(0.0, min(max_y, y_norm * src_h - out_h / 2.0))))
        lines.append(f"{t:.3f} [enter] crop x {x};")
        lines.append(f"{t:.3f} [enter] crop y {y};")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def plan(video, start, end, mode="track", src_w=1920, src_h=1080, out_w=1080, out_h=1920):
    """Decides how to reframe a clip and returns a render plan.

    Returns dict:
        mode, crop_path (list), sendcmd (path or None), reason (str)
    """
    mode = (mode or "track").lower()
    if mode not in MODES:
        mode = "track"

    if mode == "screencast":
        return {"mode": mode, "crop_path": [], "sendcmd": None,
                "reason": "Screen content is fitted and padded, never cropped."}

    if mode == "general":
        return {"mode": mode, "crop_path": [], "sendcmd": None,
                "reason": "Blur-fill background keeps the whole frame visible."}

    samples = sample_detections(video, start, end)
    faces_per_sample = [len(f) for _, f in samples]
    max_faces = max(faces_per_sample) if faces_per_sample else 0

    if mode == "split" or (mode == "track" and max_faces >= 2 and
                           sum(1 for n in faces_per_sample if n >= 2) > len(faces_per_sample) * 0.4):
        return {"mode": "split", "crop_path": [], "sendcmd": None,
                "reason": "Two speakers detected; stacking them instead of cropping one out."}

    crop_path = build_crop_path(samples)
    if not crop_path:
        return {"mode": "general", "crop_path": [], "sendcmd": None,
                "reason": "No face found; using blur-fill so nothing is cut off."}

    return {"mode": "track", "crop_path": crop_path, "sendcmd": None,
            "reason": f"Tracking the speaker across {len(crop_path)} sampled frames."}
