"""Stage 3 - scene detection.

Cuts that land mid-shot look amateur. PySceneDetect gives us the shot
boundaries so a clip can start on a cut instead of two frames into one.

Detection is best-effort: if it fails or finds nothing, the pipeline continues
with an empty list and moment selection falls back to word boundaries only.
"""
import os


def detect(video, threshold=27.0, min_scene_len_sec=0.6, fps=30.0):
    """Returns a sorted list of scene start times in seconds.

    Always includes 0.0 so the first shot is addressable.
    """
    if not video or not os.path.exists(video):
        return [0.0]

    try:
        from scenedetect import ContentDetector, SceneManager, open_video
    except ImportError:
        print("[SCENES] PySceneDetect not installed; skipping scene detection.")
        return [0.0]

    try:
        vid = open_video(video)
        manager = SceneManager()
        manager.add_detector(
            ContentDetector(
                threshold=threshold,
                min_scene_len=max(1, int(min_scene_len_sec * (fps or 30.0))),
            )
        )
        manager.detect_scenes(vid, show_progress=False)
        scene_list = manager.get_scene_list()
    except Exception as e:  # noqa: BLE001 - detection is optional
        print(f"[SCENES] Detection failed ({e}); continuing without scene data.")
        return [0.0]

    starts = sorted({round(s[0].get_seconds(), 3) for s in scene_list})
    if not starts or starts[0] > 0.01:
        starts.insert(0, 0.0)
    return starts


def nearest_cut(t, cuts, tolerance=0.75):
    """Returns the scene cut closest to t within tolerance, else None."""
    if not cuts:
        return None
    best = min(cuts, key=lambda c: abs(c - t))
    return best if abs(best - t) <= tolerance else None
