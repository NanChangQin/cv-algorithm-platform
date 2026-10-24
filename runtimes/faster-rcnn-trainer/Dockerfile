ARG TRAINING_BASE_IMAGE=cv-platform/yolo-trainer:dev
FROM ${TRAINING_BASE_IMAGE}
USER root
RUN python -m pip install --no-cache-dir pycocotools==2.0.11
COPY packages/algorithm-sdk /workspace/packages/algorithm-sdk
RUN python -m pip install --no-deps /workspace/packages/algorithm-sdk
COPY runtimes/faster-rcnn-trainer/train.py /app/faster_train.py
COPY runtimes/faster-rcnn-trainer/assets/fasterrcnn_resnet50_fpn.pt /opt/weights/fasterrcnn_resnet50_fpn.pt
RUN python -c "import hashlib; p='/opt/weights/fasterrcnn_resnet50_fpn.pt'; h=hashlib.sha256(open(p,'rb').read()).hexdigest(); assert h.startswith('258fb6c6'),h"
LABEL cv.platform.parameter-contract="torchvision:0.25.0:fasterrcnn-resnet50-fpn:v1" \
      cv.platform.training-protocol="torchvision:0.25.0:fasterrcnn-sources-v1" \
      cv.platform.model-selection="v1"
USER 10001
ENTRYPOINT ["python", "/app/faster_train.py"]
