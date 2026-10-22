"""直接访问某个节点 Algorithm Manager 的客户端：训练、镜像检查和大文件传输。"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx

from ...core.errors import ApplicationError
from ..instances.infrastructure.http_gateway import manager_headers

CHUNK_BYTES = 1024 * 1024
ProgressCallback = Callable[[int, int | None], None]


class NodeClient:
    def __init__(
        self,
        node_id: str,
        node_name: str,
        base_url: str,
        api_token: str | None = None,
        timeout: float = 30,
    ) -> None:
        self.node_id = node_id
        self.node_name = node_name
        self._base_url = base_url.rstrip("/")
        self._headers = manager_headers(api_token)
        self._timeout = timeout

    # 通用请求 -------------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
        allow_missing: bool = False,
    ) -> Any:
        try:
            response = httpx.request(
                method,
                f"{self._base_url}{path}",
                json=json,
                params=params,
                headers=self._headers,
                timeout=timeout or self._timeout,
            )
        except httpx.HTTPError as exc:
            raise self._unreachable() from exc
        if allow_missing and response.status_code == 404:
            return None
        self._raise_for_error(response)
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    def upload(
        self,
        path: str,
        source: Path,
        *,
        headers: dict[str, str] | None = None,
        progress: ProgressCallback | None = None,
    ) -> Any:
        total = source.stat().st_size

        def chunks() -> Iterator[bytes]:
            sent = 0
            with source.open("rb") as reader:
                while chunk := reader.read(CHUNK_BYTES):
                    sent += len(chunk)
                    if progress:
                        progress(sent, total)
                    yield chunk

        try:
            response = httpx.put(
                f"{self._base_url}{path}",
                content=chunks(),
                headers={
                    **self._headers,
                    **(headers or {}),
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(total),
                },
                timeout=httpx.Timeout(self._timeout, write=600, read=1800),
            )
        except httpx.HTTPError as exc:
            raise self._unreachable() from exc
        self._raise_for_error(response)
        return response.json() if response.content else None

    def download(self, path: str, target: Path) -> int:
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(f".{target.name}.partial")
        size = 0
        try:
            with httpx.stream(
                "GET",
                f"{self._base_url}{path}",
                headers=self._headers,
                timeout=httpx.Timeout(self._timeout, read=600),
            ) as response:
                if response.is_error:
                    response.read()
                    self._raise_for_error(response)
                with partial.open("wb") as writer:
                    for chunk in response.iter_bytes(CHUNK_BYTES):
                        size += len(chunk)
                        writer.write(chunk)
            partial.replace(target)
        except httpx.HTTPError as exc:
            raise self._unreachable() from exc
        finally:
            partial.unlink(missing_ok=True)
        return size

    # 节点能力 -------------------------------------------------------------

    def gpus(self) -> list[dict[str, Any]]:
        return self.request("GET", "/system/gpus")

    def image_exists(self, image: str) -> bool:
        payload = self.request("GET", "/images/inspect", params={"image": image})
        return bool(payload.get("exists"))

    def _unreachable(self) -> ApplicationError:
        return ApplicationError(
            "RUNTIME_NODE_UNAVAILABLE", f"无法连接服务器：{self.node_name}", 503
        )

    @staticmethod
    def _raise_for_error(response: httpx.Response) -> None:
        if not response.is_error:
            return
        try:
            body = response.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        message = body.get("message")
        if message is None and isinstance(body.get("detail"), list):
            message = "请求参数不符合节点要求"
        raise ApplicationError(
            str(body.get("code", "NODE_REQUEST_FAILED")),
            str(message or f"节点返回错误（HTTP {response.status_code}）"),
            502 if response.status_code >= 500 else response.status_code,
        )
