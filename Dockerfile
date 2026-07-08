# --- Build Stage ---
FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# --- Final Run Stage ---
FROM python:3.11-slim AS runner

# Установка Docker CLI для работы с контейнерами хоста
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && curl -fsSL https://get.docker.com -o get-docker.sh \
    && sh get-docker.sh \
    && apt-get purge -y curl \
    && rm -rf /var/lib/apt/lists/* \
    && rm get-docker.sh

WORKDIR /app

# Копируем установленные зависимости из сборщика
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

# Копируем исходный код
COPY . .

# Отключаем буферизацию вывода Python для корректной передачи логов в Docker log
ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]