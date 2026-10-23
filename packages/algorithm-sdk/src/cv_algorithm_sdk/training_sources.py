"""Controlled training inputs, independent of Ultralytics and torch."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

TRAINING_PROTOCOL = "ultralytics:8.4.183:sources-v1"
# Resume retains the original schedule/optimizer/dataset. Only resource knobs may change.
RESUME_OVERRIDES = frozenset({"batch", "imgsz", "workers", "cache"})


class TrainingSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["builtin", "upload", "algorithm", "run"] = "builtin"
    reference: UUID | None = None
    checkpoint: Literal["best", "last", "resume"] = "best"
    mode: Literal["finetune", "resume"] = "finetune"

    @model_validator(mode="after")
    def check_source(self):
        if (self.kind == "builtin") != (self.reference is None):
            raise ValueError("非内置模型必须指定来源 ID")
        if self.mode == "resume" and (self.kind != "run" or self.checkpoint != "resume"):
            raise ValueError("断点续训必须选择历史训练的完整恢复 checkpoint")
        if self.checkpoint == "resume" and self.mode != "resume":
            raise ValueError("微调请选择 best 或 last 权重")
        return self


class TrainingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    resume: bool = False
    epoch: int = Field(default=0, ge=0)
    epochs: int = Field(default=0, ge=0)
    optimizer: str | None = Field(default=None, max_length=64)
    lr0: float | None = Field(default=None, ge=0)
    best_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
