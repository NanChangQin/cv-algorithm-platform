FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    YOLO_AUTOINSTALL=false YOLO_CONFIG_DIR=/tmp/ultralytics MPLCONFIGDIR=/tmp/matplotlib
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY packages/algorithm-sdk /workspace/packages/algorithm-sdk
COPY examples/yolo /workspace/examples/yolo
RUN pip install --no-cache-dir torch==2.10.0 torchvision==0.25.0 --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir ultralytics==8.4.183 onnxruntime==1.23.2 \
        /workspace/packages/algorithm-sdk /workspace/examples/yolo
COPY runtimes/yolo-onnx/service.py /app/service.py
LABEL cv.platform.runtime-profile="cv-runtime-profile-v1"
RUN useradd --create-home --uid 10001 algorithm
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "service:app", "--host", "0.0.0.0", "--port", "8000"]
