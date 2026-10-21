FROM node:22-bookworm-slim AS ui
WORKDIR /ui
COPY ui/package*.json ./
RUN npm ci --no-audit --no-fund
COPY ui/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS riotx
ARG RIOTX_VERSION=1.15.1
ARG RIOTX_SHA256=109b7fc9cf9de3431f6559c3a35444feba3f8d781b3bb5038a3fa96f0b91e973
RUN python -c "import urllib.request,hashlib,zipfile; urllib.request.urlretrieve('https://github.com/redis/riotx-dist/releases/download/v${RIOTX_VERSION}/riotx-${RIOTX_VERSION}.zip','/tmp/riotx.zip'); actual=hashlib.sha256(open('/tmp/riotx.zip','rb').read()).hexdigest(); assert actual=='${RIOTX_SHA256}',actual; zipfile.ZipFile('/tmp/riotx.zip').extractall('/opt');"

FROM eclipse-temurin:21-jre-jammy AS java
FROM python:3.12-slim-bookworm
ENV JAVA_HOME=/opt/java/openjdk PATH="/opt/riotx/bin:/opt/java/openjdk/bin:$PATH" PYTHONUNBUFFERED=1 DATA_DIR=/data
COPY --from=java /opt/java/openjdk /opt/java/openjdk
COPY --from=riotx /opt/riotx-1.15.1 /opt/riotx
RUN chmod +x /opt/riotx/bin/riotx && useradd --uid 10001 --create-home workbench && mkdir /data && chown workbench /data
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./backend/
COPY runtime.json ./
COPY --from=ui /ui/dist/client ./static/
USER workbench
EXPOSE 8088
HEALTHCHECK --interval=15s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8088/api/health')"
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8088", "--workers", "1"]
