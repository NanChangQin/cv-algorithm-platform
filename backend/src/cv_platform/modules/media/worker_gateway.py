from uuid import UUID

import httpx

from ...core.errors import ApplicationError


class MediaWorkerGateway:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def extract(
        self,
        source_uri: str,
        interval_seconds: float,
        max_frames: int,
        extraction_id: UUID | None = None,
    ) -> list[dict[str, object]]:
        try:
            response = httpx.post(
                f"{self._base_url}/extract",
                json={
                    "source_uri": source_uri,
                    "interval_seconds": interval_seconds,
                    "max_frames": max_frames,
                    **(
                        {"extraction_id": str(extraction_id)}
                        if extraction_id is not None
                        else {}
                    ),
                },
                timeout=3600,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            raise ApplicationError("MEDIA_WORKER_FAILED", "媒体帧提取失败", 502) from exc
