# PhoneHome server image. Build from the repository root:
#   docker build -f deploy/docker/Dockerfile -t phonehome-server .

# 1. The web app: SvelteKit, built to static files. Node is only used here.
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# 2. The server, with the built web app and local transcription.
FROM python:3.12-slim-bookworm
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libsndfile1 tini tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY server/ /app/server/
COPY --from=web /web/build /app/server/src/phonehome_server/web/
RUN pip install --no-cache-dir "/app/server[transcribe]" && rm -rf /app/server

ENV PHONEHOME_ARCHIVE_DIR=/archive \
    PHONEHOME_DATA_DIR=/data \
    PHONEHOME_PORT=8080 \
    # The Whisper model downloads here on first use (about 1.6 GB for large-v3-turbo).
    HF_HOME=/data/models/huggingface \
    PYTHONUNBUFFERED=1
VOLUME ["/archive", "/data"]
EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4).status == 200 else 1)"

ENTRYPOINT ["tini", "--"]
CMD ["phonehome-server", "serve"]
