import os
import tempfile
import pytest
from captions import build_word_chunks, make_ass, make_srt

def test_build_word_chunks_respects_max_words():
    words = [
        {"word": f"word{i}", "start": i * 0.3, "end": (i + 1) * 0.3}
        for i in range(10)
    ]
    chunks = build_word_chunks(words, max_words=3, max_gap=0.45)
    # Should have lengths 3, 3, 3, 1
    assert len(chunks) == 4
    assert len(chunks[0]) == 3
    assert len(chunks[1]) == 3
    assert len(chunks[2]) == 3
    assert len(chunks[3]) == 1

def test_build_word_chunks_breaks_on_silence_gap():
    words = [
        {"word": "Hello", "start": 1.0, "end": 1.3},
        {"word": "world", "start": 1.3, "end": 1.6},
        # 2-second pause of silence
        {"word": "welcome", "start": 3.6, "end": 4.0},
        {"word": "back", "start": 4.0, "end": 4.3},
    ]
    chunks = build_word_chunks(words, max_words=4, max_gap=0.45)
    # Even though max_words=4, silence between 1.6 and 3.6 (> 0.45s) must break into 2 cards
    assert len(chunks) == 2
    assert [w["word"] for w in chunks[0]] == ["Hello", "world"]
    assert [w["word"] for w in chunks[1]] == ["welcome", "back"]

def test_build_word_chunks_breaks_on_punctuation():
    words = [
        {"word": "Stop.", "start": 1.0, "end": 1.3},
        {"word": "Now", "start": 1.4, "end": 1.7},
        {"word": "listen!", "start": 1.7, "end": 2.0},
        {"word": "Okay", "start": 2.1, "end": 2.3},
    ]
    chunks = build_word_chunks(words, max_words=5, max_gap=0.45)
    assert len(chunks) == 3
    assert [w["word"] for w in chunks[0]] == ["Stop."]
    assert [w["word"] for w in chunks[1]] == ["Now", "listen!"]
    assert [w["word"] for w in chunks[2]] == ["Okay"]

def test_make_ass_generates_karaoke_tags():
    segs = [{
        "start": 0.5,
        "end": 1.5,
        "text": "Never give up",
        "words": [
            {"word": "Never", "start": 0.5, "end": 0.9},
            {"word": "give", "start": 0.9, "end": 1.2},
            {"word": "up", "start": 1.2, "end": 1.5},
        ]
    }]
    with tempfile.NamedTemporaryFile("w", suffix=".ass", delete=False) as f:
        ass_path = f.name
    try:
        ok = make_ass(segs, 0.0, 2.0, ass_path, style_key="beasty", aspect_ratio="9:16")
        assert ok == ass_path
        with open(ass_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "[Script Info]" in content
        assert "[Events]" in content
        assert "Dialogue:" in content
        assert "\\k" in content
        assert "NEVER" in content or "Never" in content
    finally:
        if os.path.exists(ass_path):
            os.remove(ass_path)
