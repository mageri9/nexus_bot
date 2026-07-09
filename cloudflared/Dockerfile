FROM python:3.11-slim

# Установка необходимых утилит для скачивания бинарного файла
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Загрузка актуального бинарного релиза cloudflared для архитектуры linux-amd64
RUN curl -L \
    https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
    -o /usr/local/bin/cloudflared \
    && chmod +x /usr/local/bin/cloudflared

# Установка python-зависимостей для скрипта синхронизации вебхука
RUN pip install --no-cache-dir requests

# Копирование управляющего скрипта
COPY sync_webhook.py /sync_webhook.py

ENTRYPOINT ["python", "/sync_webhook.py"]