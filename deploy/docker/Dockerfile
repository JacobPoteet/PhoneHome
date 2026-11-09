# PhoneHome server image. Build from the repository root:
#   docker build -f deploy/docker/Dockerfile -t phonehome-server .
FROM python:3.12-slim-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libsndfile1 tini tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY server/ /app/server/
RUN pip install --no-cache-dir /app/server && rm -rf /app/server

ENV PHONEHOME_ARCHIVE_DIR=/archive \
    PHONEHOME_DATA_DIR=/data \
    PHONEHOME_PORT=8080 \
    PYTHONUNBUFFERED=1
VOLUME ["/archive", "/data"]
EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4).status == 200 else 1)"

ENTRYPOINT ["tini", "--"]
CMD ["phonehome-server", "serve"]
