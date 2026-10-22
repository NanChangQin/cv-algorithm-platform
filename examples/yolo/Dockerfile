# 与训练镜像（runtimes/yolo-trainer）使用同一 PyTorch / CUDA / Ultralytics 版本，
# 训练出的 YOLO26 / YOLO11 权重可以直接加载；CUDA 12.8 支持 RTX 50 系列。
FROM pytorch/pytorch:2.10.0-cuda12.8-cudnn9-runtime

ARG ULTRALYTICS_VERSION=8.4.183
ARG PIP_INDEX_URL=https://pypi.org/simple
# 基础镜像使用 Ubuntu 24.04 的系统 Python（PEP 668），容器内直接安装依赖
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_BREAK_SYSTEM_PACKAGES=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY packages/algorithm-sdk /workspace/packages/algorithm-sdk
COPY examples/yolo /workspace/examples/yolo

RUN pip install --no-cache-dir "ultralytics==${ULTRALYTICS_VERSION}" \
    /workspace/packages/algorithm-sdk \
    /workspace/examples/yolo

COPY examples/yolo/manifest.yaml /app/manifest.yaml
COPY examples/yolo/service.py /app/service.py

# 训练发布的版本把 manifest.yaml 与 model.pt 一起放在 /models，优先使用
ENV CV_YOLO_MODE=ultralytics \
    CV_YOLO_WEIGHTS=/models/model.pt \
    CV_YOLO_DATA_ROOT=/data \
    CV_YOLO_DEVICE=auto \
    YOLO_CONFIG_DIR=/tmp/ultralytics \
    MPLCONFIGDIR=/tmp/matplotlib

EXPOSE 8000
RUN useradd --create-home --uid 10001 algorithm && chown -R algorithm:algorithm /app
USER 10001
HEALTHCHECK --interval=15s --timeout=3s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"
CMD ["uvicorn", "service:app", "--host", "0.0.0.0", "--port", "8000"]
