# ClipForge
Paste a YouTube link, get vertical clips with captions + hashtags. No watermark.

Env vars (set any AI key): GEMINI_API_KEY, GROQ_API_KEY, XAI_API_KEY
Optional: WHISPER_MODEL (tiny/base/small), YT_COOKIES (path to cookies.txt)

Local: pip install -r requirements.txt && python server.py  (needs FFmpeg)
Render: New Web Service -> connect repo -> Runtime: Docker -> add env vars.
