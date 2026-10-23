"""Deployment settings shared by control plane, Manager and inference adapters."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

PROTOCOL = "cv-runtime-profile-v1"


class RuntimeProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    memory_mb: int | None = Field(default=None, ge=256, le=262144)
    cpu_count: float = Field(default=1, ge=0.1, le=64, allow_inf_nan=False)
    shm_mb: int = Field(default=64, ge=16, le=16384)
    prediction_timeout_seconds: int = Field(default=120, ge=1, le=600)
    # Only the ONNX CPU adapter implements these startup settings.
    threads: int = Field(default=1, ge=1, le=64)
    warmup_runs: int = Field(default=1, ge=1, le=10)
    defaults: dict[str, Any] = Field(default_factory=dict)
    allowed_overrides: list[str] | None = None

    def resolve(self, manifest, supplied: dict[str, Any]) -> dict[str, Any]:
        baseline = manifest.resolve_parameters(self.defaults)
        resolved = manifest.resolve_parameters({**self.defaults, **supplied})
        allowed = self.allowed_overrides
        if allowed is not None:
            unknown = set(allowed) - set(manifest.parameters)
            if unknown:
                raise ValueError(f"unknown override parameters: {sorted(unknown)}")
            locked = [key for key in supplied if key not in allowed
                      and resolved[key] != baseline[key]]
            if locked:
                raise ValueError(f"deployment parameters are locked: {sorted(locked)}")
        return resolved
