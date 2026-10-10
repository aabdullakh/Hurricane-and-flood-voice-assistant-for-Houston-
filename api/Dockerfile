FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /srv

COPY api/requirements.txt api/requirements.txt
RUN pip install --no-cache-dir -r api/requirements.txt

COPY api api
COPY web web
COPY data data

# Non-root user with a fixed id (docker-compose.yml runs as 10001). Code stays owned by root, read-only to the app
# (a+rX: readable whatever the file modes were on the server it was built on).
RUN chmod -R a+rX /srv && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
USER app
WORKDIR /srv/api

EXPOSE 8000
# --proxy-headers: take the client IP from Caddy's X-Forwarded-For. Safe only because port 8000 is not
# published; the only thing that can reach it is Caddy, which overwrites that header.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header"]
