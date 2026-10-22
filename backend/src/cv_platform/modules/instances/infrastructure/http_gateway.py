from datetime import datetime

import httpx

from ....core.errors import ApplicationError
from ..domain.models import RuntimeInstance, RuntimeRequest


def manager_headers(api_token: str | None) -> dict[str, str]:
    """Algorithm Manager 访问令牌请求头；未配置令牌时为空。"""
    return {"Authorization": f"Bearer {api_token}"} if api_token else {}


class HttpAlgorithmManagerGateway:
    def __init__(
        self,
        base_url: str,
        timeout: float = 75,
        node_id: str = "local",
        api_token: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._node_id = node_id
        self._headers = manager_headers(api_token)

    def list_instances(self) -> list[RuntimeInstance]:
        payload = self._request("GET", "/instances")
        return [self._to_instance(item) for item in payload]

    def ensure_running(self, request: RuntimeRequest) -> RuntimeInstance:
        payload = self._request(
            "POST",
            "/instances/ensure",
            json=self._request_payload(request),
        )
        return self._to_instance(payload)

    def ensure_replicas(
        self, request: RuntimeRequest, replicas: int
    ) -> list[RuntimeInstance]:
        payload = self._request(
            "POST",
            "/instances/replicas/ensure",
            json={**self._request_payload(request), "replicas": replicas},
        )
        return [self._to_instance(item) for item in payload]

    def stop(self, instance_id: str) -> RuntimeInstance:
        return self._to_instance(self._request("POST", f"/instances/{instance_id}/stop"))

    def remove(self, instance_id: str) -> None:
        self._request("DELETE", f"/instances/{instance_id}", expect_json=False)

    def logs(self, instance_id: str, tail: int = 200) -> list[str]:
        payload = self._request("GET", f"/instances/{instance_id}/logs?tail={tail}")
        return [str(line) for line in payload]

    def list_gpus(self) -> list[dict[str, object]]:
        return self._request("GET", "/system/gpus")

    def touch(self, instance_id: str) -> RuntimeInstance:
        return self._to_instance(self._request("POST", f"/instances/{instance_id}/touch"))

    @staticmethod
    def _request_payload(request: RuntimeRequest) -> dict[str, object]:
        return {
            "algorithm_version_id": request.algorithm_version_id,
            "algorithm_key": request.algorithm_key,
            "image": request.image,
            "container_name": request.container_name,
            "internal_port": request.internal_port,
            "device": request.device,
            "memory_mb": request.memory_mb,
            "cpu_count": request.cpu_count,
            "gpu_device_ids": list(request.gpu_device_ids),
            **({"model_directory": request.model_directory} if request.model_directory else {}),
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, object] | None = None,
        expect_json: bool = True,
    ) -> object:
        try:
            response = httpx.request(
                method,
                f"{self._base_url}{path}",
                json=json,
                headers=self._headers,
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise ApplicationError(
                "ALGORITHM_MANAGER_UNAVAILABLE",
                "算法容器管理服务不可用",
                503,
            ) from exc
        if response.is_error:
            is_json = response.headers.get("content-type", "").startswith("application/json")
            body = response.json() if is_json else {}
            raise ApplicationError(
                body.get("code", "ALGORITHM_MANAGER_ERROR"),
                body.get("message", "算法容器管理失败"),
                response.status_code,
            )
        return response.json() if expect_json else None

    def _to_instance(self, payload: dict[str, object]) -> RuntimeInstance:
        return RuntimeInstance(
            id=str(payload["id"]),
            algorithm_version_id=str(payload["algorithm_version_id"]),
            image=str(payload["image"]),
            container_name=str(payload["container_name"]),
            endpoint=(
                str(payload["endpoint"]) if self._node_id == "local"
                else f"{self._base_url}/instances/{payload['id']}"
            ),
            status=str(payload["status"]),
            device=str(payload["device"]),
            created_at=datetime.fromisoformat(str(payload["created_at"])),
            updated_at=datetime.fromisoformat(str(payload["updated_at"])),
            error=str(payload["error"]) if payload.get("error") is not None else None,
            last_used_at=(
                datetime.fromisoformat(str(payload["last_used_at"]))
                if payload.get("last_used_at") is not None
                else None
            ),
            node_id=self._node_id,
            gpu_device_ids=tuple(str(item) for item in payload.get("gpu_device_ids", [])),
        )
