"""Tests for the v3 clipping engine.

These cover the logic that decides output quality - word snapping, crop-path
smoothing, caption timing, render graph construction - without needing an
encoder. Anything that requires ffmpeg to actually write a file is marked
`slow` and skipped when ffmpeg is unavailable.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import captions, moments, reframe, render, scenes  # noqa: E402


# --------------------------------------------------------------------- fixtures
def make_segments(n=200, word_dur=0.35, gap=0.05):
    """A synthetic transcript with evenly spaced words."""
    segs, t = [], 0.0
    for i in range(n):
        w = {"word": f"w{i}", "start": round(t, 2), "end": round(t + word_dur, 2)}
        segs.append({
            "start": round(t, 2), "end": round(t + word_dur, 2),
            "text": f"w{i}", "words": [w],
        })
        t += word_dur + gap
    return segs


# ------------------------------------------------------------------ word snapping
class TestSnapToWords:
    def test_start_lands_on_a_word_boundary(self):
        segs = make_segments()
        starts = {round(s["start"], 2) for s in segs}
        s, _e = moments.snap_to_words(segs, 10.13, 40.0, 20, 60)
        assert any(abs(s - x) < 0.25 for x in starts), f"{s} is not near a word start"

    def test_end_lands_on_a_word_end(self):
        segs = make_segments()
        ends = {round(s["end"] + 0.18, 2) for s in segs}
        _s, e = moments.snap_to_words(segs, 10.0, 40.07, 20, 60)
        assert any(abs(e - x) < 0.25 for x in ends), f"{e} is not near a word end"

    @pytest.mark.parametrize("lo,hi", [(20, 60), (15, 30), (60, 180)])
    def test_duration_bounds_respected(self, lo, hi):
        segs = make_segments(600)
        s, e = moments.snap_to_words(segs, 5.0, 5.0 + hi * 2, lo, hi)
        assert lo - 1.5 <= (e - s) <= hi + 1.5

    def test_empty_transcript_does_not_crash(self):
        s, e = moments.snap_to_words([], 0.0, 30.0, 20, 60)
        assert e > s

    def test_prefers_a_scene_cut_near_the_start(self):
        segs = make_segments()
        s, _e = moments.snap_to_words(segs, 10.0, 40.0, 20, 60, cuts=[0.0, 10.2])
        assert abs(s - 10.2) < 0.3


# ------------------------------------------------------------------ crop path
class TestCropPath:
    def _samples(self, n=60, dropout=None):
        out = []
        for i in range(n):
            t = round(i * 0.166, 3)
            if dropout and dropout[0] <= i < dropout[1]:
                out.append((t, []))
                continue
            cx = 0.30 + 0.35 * (i / n) + 0.02 * ((i % 3) - 1)
            out.append((t, [(cx, 0.42, 0.12, 0.18, 0.95)]))
        return out

    def test_smoother_than_raw_detections(self):
        samples = self._samples()
        path = reframe.build_crop_path(samples, smoothing=0.18)
        xs = [p[1] for p in path]
        raw = [s[1][0][0] for s in samples if s[1]]

        def accel(v):
            return sum(abs(v[i + 1] - 2 * v[i] + v[i - 1]) for i in range(1, len(v) - 1)) / (len(v) - 2)

        assert accel(xs) < accel(raw) * 0.5, "crop path should be much smoother than raw"

    def test_follows_the_subject_drift(self):
        path = reframe.build_crop_path(self._samples())
        assert path[-1][1] > path[0][1], "path should follow a left-to-right drift"

    def test_no_jump_across_a_detection_dropout(self):
        path = reframe.build_crop_path(self._samples(dropout=(20, 26)))
        xs = [p[1] for p in path]
        steps = [abs(xs[i + 1] - xs[i]) for i in range(len(xs) - 1)]
        assert max(steps) < 0.06, f"max step {max(steps):.3f} is a visible jump"

    def test_returns_empty_when_no_faces(self):
        assert reframe.build_crop_path([(0, []), (1, [])]) == []

    def test_output_is_clamped_to_frame(self):
        samples = [(i * 0.1, [(1.4, -0.2, 0.1, 0.1, 0.9)]) for i in range(10)]
        for _t, x, y in reframe.build_crop_path(samples):
            assert 0.0 <= x <= 1.0
            assert 0.0 <= y <= 1.0

    def test_face_weight_prefers_confident_centred_face(self):
        real = (0.69, 0.30, 0.15, 0.26, 0.97)
        lamp = (0.21, 0.19, 0.16, 0.29, 0.55)
        assert reframe._face_weight(real) > reframe._face_weight(lamp)

    def test_face_weight_penalises_top_edge(self):
        centred = (0.5, 0.45, 0.15, 0.25, 0.9)
        top = (0.5, 0.10, 0.15, 0.25, 0.9)
        assert reframe._face_weight(centred) > reframe._face_weight(top)


# ------------------------------------------------------------------- sendcmd
class TestSendcmd:
    def test_writes_one_command_per_point(self, tmp_path):
        p = tmp_path / "c.cmd"
        path = [(0.0, 0.5, 0.5), (1.0, 0.3, 0.45)]
        reframe.write_sendcmd(str(p), path, 1080, 1920, 1920, 1080)
        text = p.read_text()
        assert text.count("crop x") == 2
        assert text.count("crop y") == 2
        assert "[enter]" in text

    def test_clamps_to_source_bounds(self, tmp_path):
        p = tmp_path / "c.cmd"
        reframe.write_sendcmd(str(p), [(0.0, 0.0, 0.0)], 1080, 1920, 1920, 1080)
        line = [l for l in p.read_text().splitlines() if "crop x" in l][0]
        x = int(line.split("crop x")[1].strip(" ;"))
        assert 0 <= x <= 1920 - 1080


# ------------------------------------------------------------------- captions
class TestCaptions:
    def _segs(self):
        return [{
            "start": 10.0, "end": 15.0, "text": "This is a viral hook statement",
            "words": [
                {"word": "This", "start": 10.0, "end": 10.4},
                {"word": "is", "start": 10.5, "end": 10.7},
                {"word": "a", "start": 10.8, "end": 11.0},
                {"word": "viral", "start": 11.2, "end": 11.8},
                {"word": "hook", "start": 11.9, "end": 12.3},
                {"word": "statement", "start": 12.4, "end": 13.0},
            ],
        }]

    def test_produces_dialogue_lines(self):
        ass = captions.build_ass(self._segs(), 10.0, 15.0, "bold_yellow")
        assert "[Script Info]" in ass
        assert ass.count("Dialogue:") >= 2

    def test_karaoke_tags_present(self):
        ass = captions.build_ass(self._segs(), 10.0, 15.0, "bold_yellow")
        assert "\\kf" in ass

    def test_timestamps_are_clip_local(self):
        ass = captions.build_ass(self._segs(), 10.0, 15.0, "bold_yellow")
        first = [l for l in ass.splitlines() if l.startswith("Dialogue:")][0]
        start = first.split(",")[1]
        assert start.startswith("0:00:00"), f"expected clip-local time, got {start}"

    def test_all_styles_render(self):
        for style in captions.STYLES:
            ass = captions.build_ass(self._segs(), 10.0, 15.0, style)
            assert "Dialogue:" in ass, f"{style} produced no dialogue"

    def test_unknown_style_falls_back(self):
        ass = captions.build_ass(self._segs(), 10.0, 15.0, "does_not_exist")
        assert "Dialogue:" in ass

    def test_braces_are_escaped(self):
        segs = [{"start": 0.0, "end": 2.0, "text": "a {b} c",
                 "words": [{"word": "{b}", "start": 0.0, "end": 1.0}]}]
        ass = captions.build_ass(segs, 0.0, 2.0, "clean_white")
        body = [l for l in ass.splitlines() if l.startswith("Dialogue:")][0]
        assert "{b}" not in body.split(",,")[-1]

    def test_synthetic_words_when_no_timings(self):
        segs = [{"start": 0.0, "end": 4.0, "text": "one two three four", "words": []}]
        ass = captions.build_ass(segs, 0.0, 4.0, "bold_yellow")
        assert ass.count("Dialogue:") >= 1

    def test_style_list_is_public(self):
        styles = captions.style_list()
        assert len(styles) == len(captions.STYLES)
        assert all({"id", "label", "swatch"} <= set(s) for s in styles)


# --------------------------------------------------------------------- render
class TestRenderGraph:
    @pytest.mark.parametrize("mode", ["track", "general", "split", "screencast", "crop"])
    def test_every_mode_builds_a_graph(self, mode):
        graph, label, _ = render.build_graph(
            mode, 1080, 1920, 1920, 1080,
            sendcmd="/tmp/x.cmd" if mode == "track" else None,
        )
        assert graph
        assert label in ("v", "vsub")

    def test_track_puts_sendcmd_first(self):
        graph, _l, _e = render.build_graph("track", 1080, 1920, 1920, 1080, sendcmd="/tmp/x.cmd")
        assert graph.index("sendcmd") < graph.index("crop")

    def test_captions_append_a_subtitles_stage(self):
        graph, label, _ = render.build_graph("crop", 1080, 1920, 1920, 1080, ass_path="/tmp/a.ass")
        assert "subtitles" in graph
        assert label == "vsub"

    def test_split_stacks_two_crops(self):
        graph, _l, _e = render.build_graph("split", 1080, 1920, 1920, 1080)
        assert "vstack" in graph

    def test_general_blur_fills(self):
        graph, _l, _e = render.build_graph("general", 1080, 1920, 1920, 1080)
        assert "boxblur" in graph and "overlay" in graph

    def test_screencast_pads_instead_of_cropping(self):
        graph, _l, _e = render.build_graph("screencast", 1080, 1920, 1920, 1080)
        assert "pad=" in graph

    def test_command_has_quality_settings(self):
        cmd = render.build_command("in.mp4", "out.mp4", 0, 30)
        assert cmd[cmd.index("-crf") + 1] == "18"
        assert "+faststart" in cmd
        assert cmd[cmd.index("-pix_fmt") + 1] == "yuv420p"

    def test_dimensions_are_even(self):
        for aspect in render.ASPECTS:
            w, h = render.dimensions(aspect)
            assert w % 2 == 0 and h % 2 == 0

    def test_unknown_aspect_falls_back_to_vertical(self):
        assert render.dimensions("nonsense") == (1080, 1920)


# --------------------------------------------------------------------- scenes
class TestScenes:
    def test_missing_file_returns_zero(self):
        assert scenes.detect("/nonexistent.mp4") == [0.0]

    def test_nearest_cut_within_tolerance(self):
        assert scenes.nearest_cut(10.2, [0.0, 10.0, 20.0], tolerance=0.5) == 10.0

    def test_nearest_cut_outside_tolerance(self):
        assert scenes.nearest_cut(15.0, [0.0, 10.0, 20.0], tolerance=0.5) is None


# -------------------------------------------------------------------- moments
class TestMomentSelection:
    def test_heuristic_returns_clips_without_an_llm(self):
        segs = make_segments(400)
        picks = moments.select(segs, count=3, min_len=10, max_len=25)
        assert picks, "heuristic should still produce clips"
        for p in picks:
            assert p["duration"] > 0
            assert p["scored"] is False

    def test_heuristic_respects_count(self):
        segs = make_segments(800)
        picks = moments.select(segs, count=2, min_len=10, max_len=20)
        assert len(picks) <= 2

    def test_empty_transcript_returns_nothing(self):
        assert moments.select([], count=5) == []

    def test_dedupe_drops_overlapping_candidates(self):
        cands = [
            {"start": 0, "end": 30, "virality_score": 90, "topic": "a"},
            {"start": 5, "end": 35, "virality_score": 85, "topic": "b"},
            {"start": 100, "end": 130, "virality_score": 80, "topic": "c"},
        ]
        out = moments._dedupe(cands, 3, 20, 60)
        assert len(out) == 2, "the overlapping pair should collapse to one"

    def test_dedupe_ranks_by_virality(self):
        cands = [
            {"start": 0, "end": 30, "virality_score": 60, "topic": "a"},
            {"start": 100, "end": 130, "virality_score": 95, "topic": "b"},
        ]
        out = moments._dedupe(cands, 1, 20, 60)
        assert out[0]["virality_score"] == 95
