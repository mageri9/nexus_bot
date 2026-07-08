FROM cloudflare/cloudflared:latest

USER root
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip \
    && pip3 install --no-cache-dir --break-system-packages requests \
    && rm -rf /var/lib/apt/lists/*

COPY sync_webhook.py /sync_webhook.py

ENTRYPOINT ["python3", "/sync_webhook.py"]