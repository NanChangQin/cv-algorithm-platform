import httpx
from cv_algorithm_sdk import AlgorithmResult
from pydantic import TypeAdapter

from ....core.errors import ApplicationError
from ...instances.infrastructure.http_gateway import manager_headers


class ManagerBuildGateway:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 3600,
        api_token: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._headers = manager_headers(api_token)

    def build(
        self,
        package_path: str,
        image: str,
        framework: str,
    ) -> tuple[str, list[str]]:
        try:
            response = httpx.post(
                f"{self._base_url}/images/build",
                headers=self._headers,
                json={
                    "package_path": package_path,
                    "image": image,
                    "framework": framework,
                },
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise ApplicationError(
                "ALGORITHM_MANAGER_UNAVAILABLE",
                "算法构建服务不可用",
                503,
            ) from exc
        if response.is_error:
            raise ApplicationError("BUILD_FAILED", "算法镜像构建失败", 502)
        payload = response.json()
        return str(payload["digest"]), [str(line) for line in payload.get("logs", [])]

    def remove_image(self, image: str) -> None:
        try:
            response = httpx.delete(
                f"{self._base_url}/images",
                headers=self._headers,
                params={"image": image},
                timeout=60,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ApplicationError("IMAGE_REMOVE_FAILED", "算法镜像删除失败", 502) from exc

    def validate_protocol(
        self,
        *,
        algorithm_version_id: str,
        algorithm_key: str,
        version: str,
        image: str,
        framework_device: str,
        memory_mb: int,
        expected_output: str,
    ) -> list[str]:
        container_name = f"cv-algorithm-test-{algorithm_version_id.replace('-', '')[:12]}"
        try:
            response = httpx.post(
                f"{self._base_url}/instances/ensure",
                headers=self._headers,
                json={
                    "algorithm_version_id": f"test-{algorithm_version_id}",
                    "algorithm_key": algorithm_key,
                    "image": image,
                    "container_name": container_name,
                    "internal_port": 8000,
                    "device": framework_device,
                    "memory_mb": memory_mb,
                    "cpu_count": 1,
                },
                timeout=180,
            )
            response.raise_for_status()
            instance = response.json()
            metadata = httpx.get(f"{instance['endpoint']}/metadata", timeout=10)
            metadata.raise_for_status()
            payload = metadata.json()
            if payload.get("algorithm_id") != algorithm_key:
                raise ValueError("metadata algorithm_id does not match manifest")
            if payload.get("version") != version:
                raise ValueError("metadata version does not match manifest")
            if payload.get("output_type") != expected_output:
                raise ValueError("metadata output_type does not match manifest")
            prediction = httpx.post(
                f"{instance['endpoint']}/predict",
                json={
                    "request_id": f"protocol-{algorithm_version_id}",
                    "input": {"asset_uri": "file:///app/test/sample.jpg"},
                    "parameters": {},
                },
                timeout=120,
            )
            prediction.raise_for_status()
            result = TypeAdapter(AlgorithmResult).validate_python(prediction.json())
            if result.type != expected_output:
                raise ValueError("predict result type does not match manifest")
            return [
                "/health ready=true",
                "/metadata 与 manifest 一致",
                "/predict 返回合法 Result 1.0",
            ]
        except (httpx.HTTPError, ValueError) as exc:
            raise ApplicationError("PROTOCOL_TEST_FAILED", "算法容器协议验收失败", 502) from exc
        finally:
            try:
                if "instance" in locals():
                    httpx.delete(
                        f"{self._base_url}/instances/{instance['id']}",
                        headers=self._headers,
                        timeout=30,
                    )
            except httpx.HTTPError:
                pass
