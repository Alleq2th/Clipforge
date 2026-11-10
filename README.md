# ClipForge

Long video in, vertical clips out. MomentRank picks the moments, SmartReframe
tracks the speaker, WordSync burns word-synced captions. 1080x1920, ready to post.

```
                                     ┌───────────────┐
  URL or file ──► ingest ──► asr ──► │  MomentRank   │ ──► reframe ──► captions ──► render
                     │        │      └───────────────┘
                     │        └─ word-level timestamps
                     └─ yt-dlp, 1080p source
```

## Why this beats a naive clipper

| Problem in naive implementations | What ClipForge does |
|---|---|
| Clips start/end mid-word | Every cut snapped to a real ASR word boundary |
| One 2-hour prompt, garbage picks | Overlapping transcript windows, scored independently |
| No reason given for a pick | Every clip carries hook score, virality score and a specific reason |
| Crop jitters and loses the speaker | MediaPipe + smoothed crop path, hashed through an ffmpeg `sendcmd` script |
| One reframe mode for every video | TRACK / GENERAL / SPLIT / SCREENCAST |
| Captions drift out of sync | ASS karaoke built from real word timings, not SRT |
| Cuts land mid-shot | PySceneDetect boundaries preferred when close |
| Blocks on one video | Job pool + separate render pool |

## Quick start

```bash
pip install -r requirements.txt      # needs ffmpeg on PATH
python app.py                        # http://localhost:5000
```

Production: `gunicorn -w 2 -k gthread --threads 8 --timeout 0 wsgi:app`

### Environment

| Variable | Default | Notes |
|---|---|---|
| `GEMINI_API_KEY` / `GROQ_API_KEY` / `XAI_API_KEY` / `OPENAI_API_KEY` | — | Any one enables MomentRank scoring. Without one, clips are evenly spaced and honestly labelled "not AI-scored". |
| `GROQ_API_KEY` | — | Also switches ASR to `whisper-large-v3-turbo` (cloud, ~10x faster than local CPU). |
| `WHISPER_MODEL` | `base` | `tiny` / `base` / `small` / `medium` / `large-v3` |
| `WHISPER_COMPUTE` | `int8` | `int8` on CPU, `float16` on GPU |
| `CLIP_COUNT` | `5` | Default clips per video |
| `MAX_VIDEOS_PER_JOB` | `5` | Sources per job |
| `MAX_CONCURRENT_JOBS` | `2` | Job-level parallelism |
| `MAX_CONCURRENT_RENDERS` | `2` | ffmpeg-level parallelism |
| `RENDER_CRF` | `18` | 18 is visually lossless for social |
| `REFRAME_SMOOTHING` | `0.18` | Lower = smoother crop, more lag |
| `YT_COOKIES` / `YT_COOKIES_TEXT` | — | Needed for age-gated or bot-checked sources |

### Models

The face detector needs one model file. Either let the app download it, or drop it in `models/`:

```bash
mkdir -p models
curl -L -o models/blaze_face_short_range.tflite \
  https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite
```

Without it, SmartReframe falls back to a Haar cascade, then to blur-fill.

## The pipeline

| Stage | Module | What it does | Weight |
|---|---|---|---|
| Ingest | `engine/ingest.py` | yt-dlp or local file, 1080p cap, ffprobe metadata | 8% |
| Transcribe | `engine/asr.py` | faster-whisper (or Groq) with word timestamps | 30% |
| Scenes | `engine/scenes.py` | PySceneDetect shot boundaries | 7% |
| MomentRank | `engine/moments.py` | Windowed LLM scoring + word snapping | 10% |
| Reframe | `engine/reframe.py` | Face tracking → smoothed crop path | — |
| Captions | `engine/captions.py` | ASS karaoke from word timings | — |
| Render | `engine/render.py` | ffmpeg graph per reframe mode | 45% |

Weights are real wall-clock cost on a 1-hour source, which is why the progress
bar moves honestly instead of jumping from 10% to 100%.

### Reframe modes

| Mode | When | What happens |
|---|---|---|
| `TRACK` | one speaker | crop window follows the primary face |
| `GENERAL` | no reliable subject | blurred fill behind the whole frame |
| `SPLIT` | two speakers | two stacked crops, top and bottom |
| `SCREENCAST` | screen recording | fits and pads, never crops content |

### Caption styles

`bold_yellow` `clean_white` `karaoke_green` `editorial_serif` `boxed` `neon_pink` `minimal_lower`

Styles are data in `engine/captions.py` — font, size, colours, outline, shadow,
margin and words-per-card. Adding one is a few lines.

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | Version, which engines have keys |
| `GET` | `/api/options` | Aspects, reframe modes, caption styles, durations, focus |
| `POST` | `/api/jobs` | Create a job (`sources[]`, `count`, `duration`, `aspect_ratio`, `reframe_mode`, `caption_style`, `focus`) |
| `GET` | `/api/jobs/:id` | Job status, per-stage progress, clips |
| `POST` | `/api/jobs/:id/cancel` | Cooperative cancel |
| `POST` | `/api/jobs/:id/restyle` | Re-render with a different caption style |
| `GET` | `/clips/:job/:file` | Serve a clip (`?dl=1` to download) |

## Tests

```bash
python -m pytest tests/ -q     # 66 tests
```

Coverage is on the parts that decide output quality: word snapping, crop-path
smoothing and jitter, face weighting, ASS timing and escaping, render graph
construction per mode, dedupe and ranking.

## Notes

- The faceless / voice-profile side of the old app (`faceless.py`,
  `voice_profiles.py`, `segments.py`) is left in the repo but is **not wired into
the new flow**. This version is clipping only.
- `server.py` is the old entrypoint, kept for reference. `app.py` is current.
