import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from ..domain.models import AlgorithmInstance, ContainerSpec, InstanceStatus


MANAGED_LABEL = "cv.platform.managed"
ALGORITHM_LABEL = "cv.platform.algorithm-version-id"
PORT_LABEL = "cv.platform.internal-port"
DEVICE_LABEL = "cv.platform.device"
GPU_IDS_LABEL = "cv.platform.gpu-device-ids"


class DockerContainerRuntime:
    def __init__(
        self,
        client: Any,
        network: str,
        host_data_root: Path,
        host_model_root: Path,
        health_timeout_seconds: float = 60,
        health_interval_seconds: float = 1,
        gpu_access_mode: str = "device-request",
    ) -> None:
        self._client = client
        self._network = network
        self._host_data_root = host_data_root.resolve()
        self._host_model_root = host_model_root.resolve()
        self._health_timeout = health_timeout_seconds
        self._health_interval = health_interval_seconds
        self._gpu_access_mode = gpu_access_mode

    @classmethod
    def from_environment(
        cls,
        network: str,
        host_data_root: Path,
        host_model_root: Path,
        health_timeout_seconds: float = 60,
        gpu_access_mode: str = "device-request",
    ) -> "DockerContainerRuntime":
        import docker

        return cls(
            docker.from_env(),
            network,
            host_data_root,
            host_model_root,
            health_timeout_seconds,
            gpu_access_mode=gpu_access_mode,
        )

    def create(self, spec: ContainerSpec) -> tuple[str, str]:
        gpu_options = {}
        if spec.device == "gpu" or spec.gpu_device_ids:
            gpu_options = self._gpu_options(spec.gpu_device_ids)

        container = self._client.containers.create(
            image=spec.image,
            name=spec.container_name,
            detach=True,
            network=self._network,
            mem_limit=f"{spec.memory_mb}m",
            nano_cpus=int(spec.cpu_count * 1_000_000_000),
            pids_limit=256,
            **gpu_options,
            privileged=False,
            read_only=True,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            user="10001:10001",
            labels={
                MANAGED_LABEL: "true",
                ALGORITHM_LABEL: spec.algorithm_version_id,
                PORT_LABEL: str(spec.internal_port),
                DEVICE_LABEL: spec.device,
                GPU_IDS_LABEL: ",".join(spec.gpu_device_ids),
            },
            volumes={
                str(self._host_data_root): {"bind": "/data", "mode": "ro"},
                str(self._safe_model_directory(spec.algorithm_key)): {
                    "bind": "/models",
                    "mode": "ro",
                },
            },
            tmpfs={
                "/tmp": "rw,noexec,nosuid,nodev,size=512m,uid=10001,gid=10001,mode=1777"
            },
            restart_policy={"Name": "unless-stopped"},
        )
        return container.id, f"http://{spec.container_name}:{spec.internal_port}"

    def _safe_model_directory(self, algorithm_key: str) -> Path:
        candidate = (self._host_model_root / algorithm_key).resolve()
        if self._host_model_root not in candidate.parents:
            raise ValueError("algorithm model directory escaped the configured model root")
        return candidate

    def discover(self) -> list[AlgorithmInstance]:
        containers = self._client.containers.list(
            all=True,
            filters={"label": f"{MANAGED_LABEL}=true"},
        )
        discovered = []
        for container in containers:
            labels = container.attrs.get("Config", {}).get("Labels", {}) or {}
            algorithm_version_id = labels.get(ALGORITHM_LABEL)
            if not algorithm_version_id:
                continue
            internal_port = int(labels.get(PORT_LABEL, "8000"))
            docker_status = container.status
            if docker_status == "running":
                status = InstanceStatus.HEALTHY
            elif docker_status in {"created", "restarting"}:
                status = InstanceStatus.STARTING
            elif docker_status == "exited":
                status = InstanceStatus.STOPPED
            else:
                status = InstanceStatus.FAILED
            created_raw = container.attrs.get("Created")
            try:
                created_at = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
            except (AttributeError, ValueError):
                created_at = datetime.now(UTC)
            discovered.append(
                AlgorithmInstance(
                    id=container.id,
                    algorithm_version_id=algorithm_version_id,
                    image=container.image.tags[0] if container.image.tags else container.image.id,
                    container_name=container.name,
                    endpoint=f"http://{container.name}:{internal_port}",
                    status=status,
                    device=labels.get(DEVICE_LABEL, "cpu"),
                    created_at=created_at,
                    updated_at=datetime.now(UTC),
                    last_used_at=datetime.now(UTC),
                    gpu_device_ids=tuple(
                        item for item in labels.get(GPU_IDS_LABEL, "").split(",") if item
                    ),
                )
            )
        return discovered

    def start(self, instance_id: str) -> None:
        self._managed_container(instance_id).start()

    def stop(self, instance_id: str) -> None:
        self._managed_container(instance_id).stop(timeout=10)

    def remove(self, instance_id: str) -> None:
        self._managed_container(instance_id).remove(force=True, v=True)

    def is_healthy(self, instance: AlgorithmInstance) -> bool:
        deadline = time.monotonic() + self._health_timeout
        while time.monotonic() < deadline:
            container = self._managed_container(instance.id)
            container.reload()
            if container.status not in {"created", "running", "restarting"}:
                return False
            if container.status == "running":
                try:
                    response = httpx.get(f"{instance.endpoint}/health", timeout=2)
                    if response.is_success and response.json().get("ready") is True:
                        return True
                except (httpx.HTTPError, ValueError):
                    pass
            time.sleep(self._health_interval)
        return False

    def probe_health(self, instance: AlgorithmInstance) -> bool:
        try:
            container = self._managed_container(instance.id)
            container.reload()
            if container.status != "running":
                return False
            response = httpx.get(f"{instance.endpoint}/health", timeout=2)
            return response.is_success and response.json().get("ready") is True
        except (httpx.HTTPError, ValueError, KeyError):
            return False

    def _managed_container(self, instance_id: str) -> Any:
        container = self._client.containers.get(instance_id)
        labels = container.attrs.get("Config", {}).get("Labels", {}) or {}
        if labels.get(MANAGED_LABEL) != "true":
            raise PermissionError("refusing to control an unmanaged container")
        return container

    def logs(self, instance_id: str, tail: int = 200) -> list[str]:
        raw = self._managed_container(instance_id).logs(tail=min(max(tail, 1), 1000))
        return raw.decode("utf-8", errors="replace").splitlines()

    def list_gpus(self) -> list[dict[str, object]]:
        try:
            result = self._client.containers.run(
                "nvidia/cuda:12.6.2-base-ubuntu22.04",
                [
                    "nvidia-smi",
                    "--query-gpu=index,name,memory.total,memory.used,utilization.gpu",
                    "--format=csv,noheader,nounits",
                ],
                remove=True,
                network_disabled=True,
                **self._gpu_options(()),
            )
        except Exception:
            return []
        gpus = []
        for line in result.decode("utf-8", errors="replace").splitlines():
            values = [item.strip() for item in line.split(",")]
            if len(values) == 5:
                gpus.append(
                    {
                        "index": int(values[0]),
                        "name": values[1],
                        "memory_total_mb": int(values[2]),
                        "memory_used_mb": int(values[3]),
                        "utilization_percent": int(values[4]),
                    }
                )
        return gpus

    def _gpu_options(self, gpu_device_ids: tuple[str, ...]) -> dict[str, Any]:
        if self._gpu_access_mode == "runtime":
            return {
                "runtime": "nvidia",
                "environment": {
                    "NVIDIA_VISIBLE_DEVICES": ",".join(gpu_device_ids) or "all",
                    "NVIDIA_DRIVER_CAPABILITIES": "compute,utility",
                },
            }
        from docker.types import DeviceRequest

        request = (
            DeviceRequest(device_ids=list(gpu_device_ids), capabilities=[["gpu"]])
            if gpu_device_ids else DeviceRequest(count=-1, capabilities=[["gpu"]])
        )
        return {"device_requests": [request]}
