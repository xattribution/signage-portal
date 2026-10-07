# Install the checksum-pinned browser dependency in a disposable build stage.
FROM python:3.12-slim-bookworm AS player-assets
WORKDIR /build
COPY install_hls.py ./install_hls.py
COPY hls-archive/ ./hls-archive/
RUN python install_hls.py

# No media decoders belong in the credential-bearing web container.
FROM python:3.12-slim-bookworm
# Explicit IANA data for validated clock zones; do not assume the slim base has it.
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt \
 && python -m pip check
COPY app ./app
COPY --from=player-assets /build/app/static/vendor/ ./app/static/vendor/
# Mount points are owned by the service user so fresh named volumes start out writable.
RUN groupadd --gid 1000 portal \
 && useradd --system --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin portal \
 && mkdir -p /data /spool /media/originals /media/work /media/rendered \
 && chown 1000:1000 /data /spool /media/originals /media/work /media/rendered \
 && chmod 0700 /data \
 && chmod -R a-w /srv
USER 1000:1000
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOME=/tmp TMPDIR=/spool FORWARDED_ALLOW_IPS=127.0.0.1
EXPOSE 51480
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:51480/healthz', timeout=3).read()"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "51480", "--proxy-headers", "--no-server-header", "--limit-concurrency", "64", "--timeout-keep-alive", "5"]
