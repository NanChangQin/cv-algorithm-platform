# YOLO 目标检测训练镜像。RTX 50 系列（Blackwell）需要 CUDA 12.8 及以上的 PyTorch 构建。
FROM pytorch/pytorch:2.10.0-cuda12.8-cudnn9-runtime

ARG ULTRALYTICS_VERSION=8.4.183
# 国内网络可传入镜像源，例如 --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG PIP_INDEX_URL=https://pypi.org/simple
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir "ultralytics==${ULTRALYTICS_VERSION}" "PyYAML>=6,<7"

# 训练容器默认断网运行：预训练权重和绘图字体在构建时准备好。
# assets/ 里已有的文件直接使用（无法访问 GitHub 的机器离线构建），缺少的再下载。
COPY runtimes/yolo-trainer/assets/ /opt/prefetched/
WORKDIR /opt/weights
RUN cp /opt/prefetched/*.pt /opt/weights/ 2>/dev/null || true \
    && mkdir -p /opt/ultralytics-assets \
    && cp /opt/prefetched/*.ttf /opt/ultralytics-assets/ 2>/dev/null || true
RUN YOLO_CONFIG_DIR=/opt/ultralytics-assets python -c "\
from ultralytics import YOLO; \
from ultralytics.utils.checks import check_font; \
[YOLO(name) for name in ('yolo26n.pt', 'yolo26s.pt', 'yolo26m.pt', 'yolo11n.pt', 'yolo11s.pt', 'yolo11m.pt')]; \
check_font('Arial.ttf'); check_font('Arial.Unicode.ttf')" \
    && rm -rf /opt/prefetched \
    && chmod -R a+rX /opt/weights /opt/ultralytics-assets

COPY runtimes/yolo-trainer/train.py /opt/trainer/train.py
RUN useradd --create-home --uid 10001 algorithm
USER 10001
CMD ["python", "/opt/trainer/train.py"]
