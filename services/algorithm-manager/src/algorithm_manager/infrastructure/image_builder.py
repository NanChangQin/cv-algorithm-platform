from pathlib import Path
from typing import Any

RUNTIME_IMAGES = {
    "pytorch": "pytorch/pytorch:2.10.0-cuda12.6-cudnn9-runtime",
    "ultralytics": "ultralytics/ultralytics:8.3.0",
    "onnx": "python:3.12-slim",
    "paddle": "paddlepaddle/paddle:3.0.0",
}


class ControlledImageBuilder:
    def __init__(self, docker_client: Any, package_root: Path, build_network: str) -> None:
        self._client = docker_client
        self._package_root = package_root.resolve()
        self._build_network = build_network

    def build(
        self,
        package_path: str,
        image: str,
        framework: str,
    ) -> tuple[str, list[str]]:
        base_image = RUNTIME_IMAGES.get(framework.lower())
        if base_image is None:
            raise ValueError(f"unsupported runtime framework: {framework}")
        source = Path(package_path).resolve()
        if self._package_root not in source.parents or not source.is_dir():
            raise ValueError("package path is outside the controlled package root")
        if not (source / "service.py").is_file():
            raise ValueError("algorithm package must contain service.py")

        dockerfile = source / ".platform.Dockerfile"
        dockerfile.write_text(self._dockerfile(base_image), encoding="utf-8")
        logs: list[str] = []
        try:
            built_image, stream = self._client.images.build(
                path=str(source),
                dockerfile=dockerfile.name,
                tag=image,
                rm=True,
                forcerm=True,
                network_mode=self._build_network,
                labels={"cv.platform.managed-image": "true"},
            )
            for event in stream:
                line = event.get("stream") or event.get("error")
                if line:
                    logs.extend(item for item in line.strip().splitlines() if item)
            return built_image.id, logs[-500:]
        finally:
            dockerfile.unlink(missing_ok=True)

    def remove(self, image: str) -> None:
        target = self._client.images.get(image)
        labels = target.labels or {}
        if labels.get("cv.platform.managed-image") != "true":
            raise PermissionError("refusing to remove an unmanaged image")
        self._client.images.remove(target.id, force=False)

    @staticmethod
    def _dockerfile(base_image: str) -> str:
        return f"""FROM {base_image}
WORKDIR /app
# 新版 PyTorch 基础镜像使用系统 Python（PEP 668），允许在容器内直接 pip 安装
ENV PIP_BREAK_SYSTEM_PACKAGES=1
COPY . /app
RUN if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi
RUN pip install --no-cache-dir fastapi uvicorn pydantic PyYAML
ENV PYTHONPATH=/app/.platform
RUN useradd --create-home --uid 10001 algorithm && chown -R algorithm:algorithm /app
USER 10001
EXPOSE 8000
CMD [\"uvicorn\", \"service:app\", \"--host\", \"0.0.0.0\", \"--port\", \"8000\"]
"""
