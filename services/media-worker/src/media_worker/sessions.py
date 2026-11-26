"""Bounded, incremental media capture. Only reachable on the internal network."""

import math
import subprocess
import time
from pathlib import Path
from threading import Event, Lock, Thread
from uuid import UUID

import cv2
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/sessions")
sessions = {}
guard = Lock()
ROOT = Path("/data/inference")


class OpenRequest(BaseModel):
    uri: str
    interval_seconds: float = Field(default=1, ge=0, le=60)
    live: bool = False


class Capture:
    def __init__(self, session_id, payload):
        from .main import resolve_source

        source = resolve_source(payload.uri)
        self.live = payload.live
        self.interval = payload.interval_seconds
        self.root = ROOT / str(session_id)
        self.root.mkdir(parents=True, exist_ok=True)
        self.cap = cv2.VideoCapture()
        if isinstance(source, int):
            self.cap.open(source)
        else:
            self.cap.open(
                source,
                cv2.CAP_FFMPEG,
                [
                    cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,
                    5000,
                    cv2.CAP_PROP_READ_TIMEOUT_MSEC,
                    5000,
                ],
            )
        if not self.cap.isOpened():
            self.cap.release()
            raise HTTPException(422, "媒体源不可连接或设备被占用")
        fps = self.cap.get(cv2.CAP_PROP_FPS)
        self.fps = fps if math.isfinite(fps) and 0 < fps <= 240 else 25.0
        self.total = (
            int(max(0, self.cap.get(cv2.CAP_PROP_FRAME_COUNT))) if not self.live else 0
        )
        self.index = -1
        self.stride = max(1, round(self.fps * self.interval))
        self.lock = Lock()
        self.stop = Event()
        self.latest = None
        self.last_read = -1
        self.failed = False
        self.touched = time.monotonic()
        self.start = time.monotonic()
        self.dropped = 0
        if self.live:
            Thread(target=self.pump, daemon=True).start()

    def pump(self):
        try:
            while not self.stop.is_set() and time.monotonic() - self.touched < 180:
                ok, frame = self.cap.read()
                if not ok:
                    self.failed = True
                    return
                with self.lock:
                    self.index += 1
                    self.latest = (
                        self.index,
                        (time.monotonic() - self.start) * 1000,
                        frame,
                    )
        finally:
            self.cap.release()

    def next(self):
        self.touched = time.monotonic()
        if self.live:
            deadline = time.monotonic() + 6
            while time.monotonic() < deadline:
                with self.lock:
                    item = self.latest
                if item and item[0] > self.last_read:
                    self.dropped += max(0, item[0] - self.last_read - 1)
                    self.last_read = item[0]
                    return self.write(*item)
                if self.failed:
                    raise HTTPException(502, "视频流中断")
                time.sleep(0.05)
            raise HTTPException(504, "等待画面超时")
        while True:
            ok, frame = self.cap.read()
            self.index += 1
            if not ok:
                return {"eof": True}
            if self.index % self.stride == 0:
                return self.write(self.index, self.index / self.fps * 1000, frame)

    def write(self, index, timestamp, frame):
        path = self.root / "capture.jpg"
        if not cv2.imwrite(str(path), frame):
            raise HTTPException(500, "画面写入失败")
        return {
            "relative_path": str(path.relative_to("/data")),
            "source_index": index,
            "timestamp_ms": timestamp,
            "fps": self.fps,
            "total_frames": self.total,
            "skipped_frames": self.dropped
            if self.live
            else max(0, index - index // self.stride),
            "eof": False,
        }

    def close(self):
        self.stop.set()
        if not self.live:
            self.cap.release()


@router.put("/{session_id}")
def open_capture(session_id: UUID, payload: OpenRequest):
    with guard:
        old = sessions.pop(session_id, None)
        if old:
            old.close()
        # Reap abandoned clients before checking capacity.
        for key, item in list(sessions.items()):
            if time.monotonic() - item.touched > 180:
                item.close()
                sessions.pop(key)
        if len(sessions) >= 4:
            raise HTTPException(429, "媒体采集并发已满")
        sessions[session_id] = Capture(session_id, payload)
    return {"opened": True}


@router.get("/{session_id}/next")
def next_frame(session_id: UUID):
    capture = sessions.get(session_id)
    if not capture:
        raise HTTPException(404, "采集会话不存在")
    return capture.next()


@router.delete("/{session_id}")
def close_capture(session_id: UUID):
    with guard:
        capture = sessions.pop(session_id, None)
    if capture:
        capture.close()
    return {"closed": True}


class VideoRequest(BaseModel):
    frames: list[dict] = Field(max_length=10000)
    raw: bool = False


@router.post("/{session_id}/video")
def encode_video(session_id: UUID, payload: VideoRequest):
    root = ROOT / str(session_id)
    name = "recording" if payload.raw else "result"
    records = []
    for frame in payload.frames:
        index = int(frame["index"])
        path = root / f"{name}-{index:08d}.jpg"
        if path.exists():
            records.append((path, float(frame["timestamp_ms"]) / 1000))
    if not records:
        raise HTTPException(422, "没有可编码的画面")
    lines = []
    for i, (path, ts) in enumerate(records):
        lines.append(f"file '{path.name}'")
        duration = records[i + 1][1] - ts if i + 1 < len(records) else 0.04
        lines.append(f"duration {max(0.001, min(duration, 60)):.6f}")
    lines.append(f"file '{records[-1][0].name}'")
    listing = root / f"{name}-frames.txt"
    listing.write_text("\n".join(lines))
    output = root / f"{name}.mp4"
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-y",
                "-v",
                "error",
                "-f",
                "concat",
                "-safe",
                "1",
                "-i",
                str(listing),
                "-vf",
                "pad=ceil(iw/2)*2:ceil(ih/2)*2",
                "-vsync",
                "vfr",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(output),
            ],
            check=True,
            timeout=300,
            capture_output=True,
        )
    except (subprocess.SubprocessError, OSError):
        raise HTTPException(500, "结果视频编码失败") from None
    return {"file": str(output.relative_to("/data")), "audio": False}


@router.get("/devices/cameras")
def cameras():
    return [
        {"id": p.name, "uri": "camera:" + p.name.removeprefix("video")}
        for p in sorted(Path("/dev").glob("video[0-9]*"))
    ]
