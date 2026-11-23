import os
import tempfile
import pytest
from transcribe import _parse_vtt, _find_instant_subtitles

SAMPLE_VTT = """WEBVTT
Kind: captions
Language: en

00:00:00.200 --> 00:00:02.500
<00:00:00.200><c> welcome</c><00:00:00.600><c> back</c><00:00:01.100><c> everyone</c>

00:00:02.500 --> 00:00:05.100
<00:00:02.500><c> to</c><00:00:02.800><c> the</c><00:00:03.200><c> ultimate</c><00:00:03.900><c> tutorial</c>

00:00:05.500 --> 00:00:08.200
welcome back everyone
<00:00:05.500><c> today</c><00:00:06.000><c> we</c><00:00:06.300><c> build</c><00:00:06.900><c> software.</c>
"""

def test_parse_vtt_extracts_words_and_deduplicates():
    with tempfile.NamedTemporaryFile("w", suffix=".en.vtt", delete=False) as f:
        f.write(SAMPLE_VTT)
        vtt_path = f.name
    try:
        segments = _parse_vtt(vtt_path)
        assert len(segments) >= 3
        # Check first segment
        s0 = segments[0]
        assert s0["start"] == 0.200
        assert s0["end"] == 2.500
        assert "welcome back everyone" in s0["text"]
        words0 = s0.get("words", [])
        assert len(words0) == 3
        assert words0[0]["text"] == "welcome"
        assert words0[0]["start"] == 0.200
        assert words0[1]["text"] == "back"
        assert words0[1]["start"] == 0.600
        assert words0[2]["text"] == "everyone"
        assert words0[2]["start"] == 1.100
        assert words0[2]["end"] <= 2.500

        # Check duplicate line rejection in segment 3
        s2 = segments[2]
        # "welcome back everyone" should have been filtered out as a repeated roll-up line
        assert "today we build software." in s2["text"]
    finally:
        if os.path.exists(vtt_path):
            os.remove(vtt_path)

def test_find_instant_subtitles_validates_parsed_quality():
    with tempfile.TemporaryDirectory() as temp_dir:
        vtt_path = os.path.join(temp_dir, "test.en.vtt")
        with open(vtt_path, "w") as f:
            f.write(SAMPLE_VTT)
        
        subs = _find_instant_subtitles(temp_dir)
        assert subs is not None
        assert len(subs) >= 3
        assert any(len(s.get("words", [])) > 0 for s in subs)
