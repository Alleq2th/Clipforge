import os
import sys

def _get_cascade_path():
    """Finds the bundled or system Haar cascade XML file."""
    # 1. Bundled in local models directory
    local_path = os.path.join(os.path.dirname(__file__), "models", "haarcascade_frontalface_default.xml")
    if os.path.exists(local_path) and os.path.getsize(local_path) > 1000:
        return local_path

    # 2. Bundled in cv2 package
    try:
        import cv2
        if hasattr(cv2, "data") and hasattr(cv2.data, "haarcascades"):
            cv_path = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
            if os.path.exists(cv_path):
                return cv_path
    except Exception:
        pass

    # 3. Common system locations
    system_paths = [
        "/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml",
        "/usr/share/opencv/haarcascades/haarcascade_frontalface_default.xml",
        "/usr/local/share/opencv4/haarcascades/haarcascade_frontalface_default.xml",
    ]
    for sp in system_paths:
        if os.path.exists(sp):
            return sp

    return None

def detect_smart_crop_anchor(video_path, start_time, end_time, sample_rate_sec=1.5, max_samples=12):
    """
    Analyzes video segment between start_time and end_time to locate the primary
    human subject / active speaker.

    Returns:
        (x_norm, y_norm, has_detection)
        x_norm: float in [0.05, 0.95], normalized horizontal focal center (0.5 is dead center)
        y_norm: float in [0.10, 0.85], normalized vertical focal center
        has_detection: bool indicating if faces were detected with confidence

    Safe Fallback Guarantee:
        If OpenCV is not installed, video cannot be read, no faces are detected,
        or any exception occurs, this function safely returns (0.5, 0.5, False).
    """
    fallback = (0.5, 0.5, False)

    if not video_path or not os.path.exists(video_path):
        return fallback

    if end_time is None or start_time is None or end_time <= start_time:
        return fallback

    try:
        import cv2
        import numpy as np
    except ImportError:
        print("[REFRAME] OpenCV not installed; falling back to center crop.")
        return fallback

    cascade_path = _get_cascade_path()
    if not cascade_path:
        print("[REFRAME] Face cascade classifier file not found; falling back to center crop.")
        return fallback

    try:
        cascade = cv2.CascadeClassifier(cascade_path)
        if cascade.empty():
            print("[REFRAME] Could not initialize CascadeClassifier; falling back to center crop.")
            return fallback
    except Exception as e:
        print(f"[REFRAME] CascadeClassifier initialization error: {e}; falling back.")
        return fallback

    duration = max(0.1, end_time - start_time)
    num_samples = min(max_samples, max(3, int(duration / sample_rate_sec)))
    step = duration / (num_samples + 1)
    timestamps = [start_time + (step * i) for i in range(1, num_samples + 1)]

    detections = []
    cap = None

    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            print(f"[REFRAME] Failed to open video {video_path} for smart reframing.")
            return fallback

        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

        for ts in timestamps:
            cap.set(cv2.CAP_PROP_POS_MSEC, ts * 1000.0)
            ret, frame = cap.read()
            if not ret or frame is None:
                continue

            orig_h, orig_w = frame.shape[:2]
            if orig_w < 10 or orig_h < 10:
                continue

            # Scale down for fast detection (10-15ms per frame)
            target_w = 480
            scale = target_w / float(orig_w)
            target_h = int(orig_h * scale)
            small = cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

            faces = cascade.detectMultiScale(
                gray,
                scaleFactor=1.1,
                minNeighbors=3,
                minSize=(25, 25)
            )

            if len(faces) > 0:
                # Rank faces by area to favor primary/closer speakers
                # (x, y, w, h)
                sorted_faces = sorted(faces, key=lambda f: f[2] * f[3], reverse=True)
                primary = sorted_faces[0]
                px, py, pw, ph = primary
                cx = (px + pw / 2.0) / float(target_w)
                cy = (py + ph / 2.0) / float(target_h)
                weight = float(pw * ph)
                detections.append((cx, cy, weight))

    except Exception as e:
        print(f"[REFRAME] Warning during face detection sampling: {e}")
        return fallback
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass

    if not detections:
        return fallback

    # Compute weighted or median center of subject across sampled frames
    xs = [d[0] for d in detections]
    ys = [d[1] for d in detections]

    # Median is robust against temporary false positives (e.g. background pattern detected once)
    median_x = float(np.median(xs))
    median_y = float(np.median(ys))

    # Clamp to safe bounds so crop window doesn't go completely out of frame
    clamped_x = max(0.10, min(0.90, median_x))
    clamped_y = max(0.10, min(0.85, median_y))

    return round(clamped_x, 4), round(clamped_y, 4), True
