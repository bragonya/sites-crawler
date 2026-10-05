FROM python:3.12-slim

WORKDIR /opt/stackline-crawling
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AI_MODE=real \
    AI_CHAT_MODEL=gpt-6-luna \
    CRAWL_BIND_HOST=0.0.0.0 \
    CRAWL_PORT=8090

COPY pyproject.toml README.md ./
COPY stackline_crawling ./stackline_crawling
RUN apt-get update \
    && apt-get install -y --no-install-recommends jq gawk \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir .

EXPOSE 8090
CMD ["python", "-m", "stackline_crawling.server"]
