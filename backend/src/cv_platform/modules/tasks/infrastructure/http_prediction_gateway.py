import json
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx
from cv_algorithm_sdk import AlgorithmResult
from pydantic import TypeAdapter, ValidationError

from ....core.errors import ApplicationError
from ...instances.domain.models import RuntimeInstance
from ...instances.infrastructure.http_gateway import manager_headers

result_adapter = TypeAdapter(AlgorithmResult)


class HttpAlgorithmPredictionGateway:
    def __init__(
        self,
        timeout_seconds: float,
        storage_root: Path = Path("storage"),
        manager_api_token: str | None = None,
    ) -> None:
        self._timeout = timeout_seconds
        self._storage_root = storage_root.resolve()
        # 远端节点的推理请求经过该节点的 Manager 代理，需要携带访问令牌
        self._manager_headers = manager_headers(manager_api_token)

    def predict(
        self,
        instance: RuntimeInstance,
        request_id: str,
        asset_uri: str,
        parameters: dict[str, object],
    ) -> AlgorithmResult:
        try:
            if instance.node_id != "local":
                source = self._resolve_input(asset_uri)
                with source.open("rb") as stream:
                    response = httpx.post(
                        f"{instance.endpoint}/predict",
                        data={"request_id": request_id, "parameters": json.dumps(parameters)},
                        files={"file": (source.name, stream, "application/octet-stream")},
                        headers=self._manager_headers,
                        timeout=self._timeout,
                    )
            else:
                response = httpx.post(
                    f"{instance.endpoint}/predict",
                    json={
                        "request_id": request_id,
                        "input": {"asset_uri": asset_uri},
                        "parameters": parameters,
                    },
                    timeout=self._timeout,
                )
        except httpx.TimeoutException as exc:
            raise ApplicationError("PREDICTION_TIMEOUT", "算法推理超时", 504) from exc
        except httpx.HTTPError as exc:
            raise ApplicationError("ALGORITHM_UNAVAILABLE", "无法连接算法容器", 502) from exc
        if response.is_error:
            try:
                detail = response.json()
            except ValueError:
                detail = {}
            raise ApplicationError(
                detail.get("code", "PREDICTION_FAILED"),
                detail.get("message", f"算法容器返回 HTTP {response.status_code}"),
                502,
            )
        try:
            return result_adapter.validate_python(response.json())
        except (ValueError, ValidationError) as exc:
            raise ApplicationError(
                "RESULT_SCHEMA_INVALID",
                "算法返回结果不符合平台 Result 1.0 协议",
                502,
            ) from exc

    def _resolve_input(self, asset_uri: str) -> Path:
        uri = urlsplit(asset_uri)
        path = unquote(uri.path)
        if uri.scheme != "file" or uri.netloc or not path.startswith("/data/"):
            raise ApplicationError("INPUT_INVALID", "远端推理仅支持平台内的输入资源")
        candidate = (self._storage_root / path.removeprefix("/data/")).resolve()
        if self._storage_root not in candidate.parents:
            raise ApplicationError("INPUT_INVALID", "输入资源路径不合法")
        if not candidate.is_file():
            raise ApplicationError("ASSET_FILE_MISSING", "输入资源文件不存在", 404)
        return candidate
