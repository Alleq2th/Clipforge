import re

import pytest

import pick


def make_segs(total_seconds, seg_len=4.0, gap=0.2):
    segs, t, i = [], 0.0, 0
    while t < total_seconds:
        segs.append({"start": round(t, 2), "end": round(t + seg_len, 2), "text": f"sentence {i}."})
        t += seg_len + gap
        i += 1
    return segs


def fake_llm(prompt):
    """Stands in for the AI: returns exactly as many candidates as the prompt asks for, evenly spread across the window."""
    n = int(re.search(r"discover up to (\d+)", prompt).group(1))
    lo, hi = map(int, re.search(r"Every clip MUST be between (\d+) and (\d+) seconds", prompt).groups())
    stamps = re.findall(r"^\[(\d+)-(\d+)\] ", prompt, re.M)
    w_start, w_end = float(stamps[0][0]), float(stamps[-1][1])
    step = (w_end - w_start) / n
    clips = []
    for k in range(n):
        s = w_start + step * k + 1.0
        dur = min(hi, max(lo, step - 2.0))
        clips.append({
            "start": round(s, 2),
            "end": round(min(s + dur, w_end), 2),
            "title": f"clip {k}",
            "main_topic": f"topic {int(w_start)}-{k}",
            "score": 90 - (k % 10),
        })
    return {"clips": clips}


def broken_llm(prompt):
    raise RuntimeError("AI unavailable")


LONG_VIDEO = make_segs(20 * 60)   # 20 minutes: several overlapping AI windows
SHORT_VIDEO = make_segs(150)      # 2.5 minutes: barely room for a handful of separate clips


@pytest.fixture
def ai(monkeypatch):
    monkeypatch.setattr(pick, "ask_json", fake_llm)


@pytest.mark.parametrize("count", [10, 15, 20])
def test_long_video_returns_exact_requested_count(ai, count):
    clips = pick.pick(LONG_VIDEO, count=count, min_len=20, max_len=60)
    assert len(clips) == count


@pytest.mark.parametrize("count", [10, 15, 20])
def test_long_video_never_exceeds_requested_count(ai, count):
    clips = pick.pick(LONG_VIDEO, count=count, min_len=20, max_len=60)
    assert len(clips) <= count


@pytest.mark.parametrize("count", [10, 15, 20])
def test_short_video_reports_shortfall_honestly(ai, count):
    clips = pick.pick(SHORT_VIDEO, count=count, min_len=20, max_len=60)
    report = pick.clip_run_report(count, len(clips), 20, 60, 150, relaxed=False)
    assert len(clips) <= count
    assert report["produced"] == len(clips)
    assert report["requested"] == count
    assert report["shortfall"] == (len(clips) < count)
    if report["shortfall"]:
        assert report["reason"] and str(len(clips)) in report["reason"]
    else:
        assert report["reason"] is None


@pytest.mark.parametrize("count", [10, 15, 20])
def test_relaxed_mode_finds_at_least_as_many_on_short_video(ai, count):
    strict = pick.pick(SHORT_VIDEO, count=count, min_len=20, max_len=60, relax=False)
    relaxed = pick.pick(SHORT_VIDEO, count=count, min_len=20, max_len=60, relax=True)
    assert len(relaxed) >= len(strict)
    assert len(relaxed) <= count


def test_relaxed_clips_stay_inside_relaxed_length_band(ai):
    clips = pick.pick(SHORT_VIDEO, count=20, min_len=20, max_len=60, relax=True)
    lo, hi = pick.effective_limits(20, 60, True)
    for c in clips:
        dur = float(c["end"]) - float(c["start"])
        assert lo - 0.5 <= dur <= hi + 0.5


def test_strict_clips_stay_inside_strict_length_band(ai):
    clips = pick.pick(LONG_VIDEO, count=20, min_len=20, max_len=60, relax=False)
    for c in clips:
        dur = float(c["end"]) - float(c["start"])
        assert 20 - 0.5 <= dur <= 60 + 0.5


@pytest.mark.parametrize("count", [10, 15, 20])
def test_ai_failure_still_returns_within_count(monkeypatch, count):
    monkeypatch.setattr(pick, "ask_json", broken_llm)
    clips = pick.pick(LONG_VIDEO, count=count, min_len=20, max_len=60)
    assert 0 < len(clips) <= count


def test_report_without_shortfall_has_no_reason():
    report = pick.clip_run_report(10, 10, 20, 60, 1200, relaxed=False)
    assert report["shortfall"] is False
    assert report["reason"] is None


def test_relaxed_report_names_the_relaxed_band():
    report = pick.clip_run_report(20, 4, 20, 60, 150, relaxed=True)
    assert report["shortfall"] is True
    assert report["min_len"] == 15 and report["max_len"] == 90
    assert "relaxed" in report["reason"].lower()
