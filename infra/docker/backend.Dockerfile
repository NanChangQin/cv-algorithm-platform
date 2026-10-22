FROM python:3.12-slim

WORKDIR /workspace

ARG PIP_INDEX_URL=https://pypi.org/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY packages/algorithm-sdk ./packages/algorithm-sdk
COPY backend ./backend

RUN python -m pip install --no-cache-dir \
    --index-url "$PIP_INDEX_URL" \
    ./packages/algorithm-sdk \
    ./backend

HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

EXPOSE 8000
# 信任前端 nginx 传来的 X-Forwarded-For（Backend 端口不对外发布），按真实来源 IP 限流
CMD ["uvicorn", "cv_platform.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
