FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn
COPY . .
ENV WHISPER_MODEL=tiny
CMD gunicorn server:app -b 0.0.0.0:${PORT:-5000} --timeout 600 --threads 8 --workers 1
