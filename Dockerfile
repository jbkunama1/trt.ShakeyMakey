FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    nginx \
    supervisor \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY bot.py .
COPY static ./static

COPY docker/nginx.conf /etc/nginx/nginx.conf
COPY docker/supervisord.conf /etc/supervisor/conf.d/supervisord.conf

ENV TEAMSHAKE_DB=/data/teamshake.db
ENV TEAMSHAKE_MAX_DRAWS=50
ENV TELEGRAM_BOT_TOKEN=""
ENV TELEGRAM_ALLOWED_IDS=""

RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://127.0.0.1/healthz || exit 1

CMD ["supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
