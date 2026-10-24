FROM python:3.12-slim

WORKDIR /workspace
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY services/media-worker ./services/media-worker
RUN python -m pip install --no-cache-dir ./services/media-worker
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8020/health')"
EXPOSE 8020
CMD ["uvicorn", "media_worker.main:app", "--host", "0.0.0.0", "--port", "8020"]
