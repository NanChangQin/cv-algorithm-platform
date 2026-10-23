"""Export verification policy shared by API, node and isolated runtime."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .runtime_contract import normalize_parameters

EXPORT_PROTOCOL = "ultralytics:8.4.183:onnx-v1"


class ExportVerification(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    parameters: dict[str, Any] = Field(default_factory=dict)
    max_map_drop: float = Field(default=0.01, ge=0, le=1)
    warmup: int = Field(default=1, ge=0, le=20)
    iterations: int = Field(default=5, ge=1, le=100)
    threads: int = Field(default=2, ge=1, le=32)

    @field_validator("parameters")
    @classmethod
    def validate_parameters(cls, value):
        return normalize_parameters("val", value)


def verification_parameters(export: dict, policy: ExportVerification) -> dict:
    """Same shape, full dataset and postprocessing for both evaluated backends."""
    expected = {
        "batch": export["batch"],
        "imgsz": export["imgsz"],
        "rect": False,
        "quantize": None,
        "fraction": 1.0,
        "compile": False,
        "augment": False,
        "channels_last": False,
        "nms": False,
    }
    supplied = policy.parameters
    for key, value in expected.items():
        if key in supplied and supplied[key] != value:
            raise ValueError(f"验证 {key} 必须为 {value}，保证同集与导出形状一致")
    return normalize_parameters(
        "val",
        {
            "split": "val",
            "workers": 0,
            "plots": False,
            "conf": 0.001,
            "iou": 0.7,
            **supplied,
            **expected,
        },
    )
