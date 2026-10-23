FROM python:3.12-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY services/algorithm-manager ./services/algorithm-manager
COPY packages/algorithm-sdk ./packages/algorithm-sdk

RUN python -m pip install --no-cache-dir ./packages/algorithm-sdk ./services/algorithm-manager

HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8010/health')"

EXPOSE 8010
CMD ["uvicorn", "algorithm_manager.main:app", "--host", "0.0.0.0", "--port", "8010"]
