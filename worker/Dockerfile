# Office/PDF/video/image decoders are isolated from the web process and secrets.
FROM python:3.12-slim-bookworm
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ffmpeg libreoffice-impress poppler-utils libcairo2 \
      fonts-dejavu fonts-liberation2 fonts-crosextra-carlito fonts-crosextra-caladea fonts-noto-core \
 && unwanted="$(dpkg-query -W -f='${binary:Package} ${db:Status-Status}\n' | awk '$2 == "installed" && $1 ~ /^(libreoffice-script-provider-|default-jre|openjdk-)/ {print $1}')" \
 && if [ -n "$unwanted" ]; then apt-get purge -y --auto-remove $unwanted; fi \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt \
 && python -m pip check
COPY convert.py storage_check.py graphics.py ./
RUN groupadd --gid 1000 worker \
 && useradd --system --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin worker \
 && chmod -R a-w /srv
USER 1000:1000
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOME=/tmp TMPDIR=/tmp
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import os,time,sys; sys.exit(time.time()-os.stat('/media/work/.heartbeat').st_mtime>30)"
CMD ["python", "/srv/convert.py"]
