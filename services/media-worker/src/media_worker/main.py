import hashlib
from pathlib import Path
from urllib.parse import unquote, urlparse
from uuid import UUID, uuid4

import cv2
from fastapi import FastAPI
from pydantic import BaseModel, Field


DATA_ROOT = Path("/data").resolve()
FRAME_ROOT = DATA_ROOT / "media-frames"
FRAME_ROOT.mkdir(parents=True, exist_ok=True)


class ExtractFramesRequest(BaseModel):
    source_uri: str = Field(min_length=1, max_length=2048)
    interval_seconds: float = Field(default=1, gt=0, le=60)
    max_frames: int = Field(default=100, ge=1, le=10_000)
    jpeg_quality: int = Field(default=90, ge=50, le=100)
    extraction_id: UUID = Field(default_factory=uuid4)


class FrameInfo(BaseModel):
    index: int
    timestamp_ms: float
    asset_uri: str
    relative_path: str


def resolve_source(uri: str) -> str | int:
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        candidate = Path(unquote(parsed.path)).resolve()
        if DATA_ROOT not in candidate.parents or not candidate.is_file():
            raise ValueError("video path is outside /data or missing")
        return str(candidate)
    if parsed.scheme == "camera" and parsed.path.isdigit():
        return int(parsed.path)
    if parsed.scheme in {"rtsp", "rtsps"}:
        return uri
    raise ValueError("unsupported media source URI")


app = FastAPI(title="CV Media Worker", version="0.1.0")


@app.get("/health")
def health() -> dict[str, object]:
    return {"status": "ok", "ready": True}


@app.post("/extract", response_model=list[FrameInfo])
def extract(payload: ExtractFramesRequest) -> list[FrameInfo]:
    source = resolve_source(payload.source_uri)
    capture = cv2.VideoCapture(source)
    if not capture.isOpened():
        raise ValueError("could not open media source")
    source_key = hashlib.sha256(payload.source_uri.encode()).hexdigest()[:16]
    target_root = FRAME_ROOT / source_key / payload.extraction_id.hex
    target_root.mkdir(parents=True, exist_ok=True)
    fps = capture.get(cv2.CAP_PROP_FPS) or 25
    frame_stride = max(1, round(fps * payload.interval_seconds))
    frames = []
    source_index = 0
    try:
        while len(frames) < payload.max_frames:
            ok, frame = capture.read()
            if not ok:
                break
            if source_index % frame_stride == 0:
                relative_path = (
                    f"media-frames/{source_key}/{payload.extraction_id.hex}/"
                    f"{len(frames):08d}.jpg"
                )
                output_path = DATA_ROOT / relative_path
                if not cv2.imwrite(
                    str(output_path),
                    frame,
                    [cv2.IMWRITE_JPEG_QUALITY, payload.jpeg_quality],
                ):
                    raise RuntimeError("failed to write extracted frame")
                frames.append(
                    FrameInfo(
                        index=len(frames),
                        timestamp_ms=(source_index / fps) * 1000,
                        asset_uri=f"file:///data/{relative_path}",
                        relative_path=relative_path,
                    )
                )
            source_index += 1
    finally:
        capture.release()
    return frames

from .sessions import router as sessions_router
app.include_router(sessions_router)
