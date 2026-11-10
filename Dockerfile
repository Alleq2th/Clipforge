FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        fonts-dejavu-core \
        libgl1 \
        libglib2.0-0 \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-fetch the face detector so the first job does not wait on a download.
RUN mkdir -p /app/models && \
    curl -fsSL -o /app/models/blaze_face_short_range.tflite \
      https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite

COPY . .

ENV WHISPER_MODEL=base \
    WHISPER_COMPUTE=int8 \
    WORK_DIR=/tmp/clipforge/work \
    OUT_DIR=/tmp/clipforge/output \
    PYTHONUNBUFFERED=1

EXPOSE 5000

# gthread + timeout 0: a long render must never be killed by the worker timeout.
CMD gunicorn wsgi:app \
      -b 0.0.0.0:${PORT:-5000} \
      -k gthread --threads 8 --workers 1 \
      --timeout 0 --graceful-timeout 30
