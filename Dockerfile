# Render deploys this image directly (see render.yaml).
FROM python:3.11-slim-bookworm

# ffmpeg  -> audio decoding for speech transcription
# exiftool -> EXIF metadata for images and audio
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        libimage-exiftool-perl \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=10000

WORKDIR /srv/app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as an unprivileged user.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /srv/app
USER appuser

EXPOSE 10000

# Conversion is CPU- and IO-bound and can take a while on big PDFs, so give
# gunicorn a generous timeout and a couple of threads per worker.
CMD ["sh", "-c", "gunicorn wsgi:app --bind 0.0.0.0:${PORT} --workers ${WEB_CONCURRENCY:-2} --threads ${WEB_THREADS:-4} --timeout ${WEB_TIMEOUT:-180} --access-logfile - --error-logfile -"]
