from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    field_validator,
    model_validator,
)


class ResultModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BoundingBox(RootModel[list[float]]):
    @model_validator(mode="after")
    def validate_box(self) -> "BoundingBox":
        if len(self.root) != 4:
            raise ValueError("bbox must contain exactly four coordinates")
        x_min, y_min, x_max, y_max = self.root
        if min(self.root) < 0:
            raise ValueError("bbox coordinates cannot be negative")
        if x_max <= x_min or y_max <= y_min:
            raise ValueError("bbox must have a positive width and height")
        return self


class Detection(ResultModel):
    label: str = Field(min_length=1, max_length=128)
    score: float = Field(ge=0, le=1)
    bbox: BoundingBox


class ObjectDetectionData(ResultModel):
    detections: list[Detection] = Field(default_factory=list, max_length=10_000)


class AlgorithmIdentity(ResultModel):
    id: str
    version: str


class ImageMetadata(ResultModel):
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class TimingMetadata(ResultModel):
    preprocess_ms: float = Field(default=0, ge=0)
    inference_ms: float = Field(default=0, ge=0)
    postprocess_ms: float = Field(default=0, ge=0)


class ObjectDetectionResult(ResultModel):
    schema_version: Literal["1.0"] = "1.0"
    request_id: str
    type: Literal["object_detection"] = "object_detection"
    algorithm: AlgorithmIdentity
    input: ImageMetadata
    timing: TimingMetadata
    data: ObjectDetectionData
    configuration: dict[str, Any] | None = None


class ClassificationPrediction(ResultModel):
    label: str = Field(min_length=1, max_length=128)
    score: float = Field(ge=0, le=1)


class ClassificationData(ResultModel):
    predictions: list[ClassificationPrediction] = Field(default_factory=list, max_length=10_000)


class ClassificationResult(ResultModel):
    schema_version: Literal["1.0"] = "1.0"
    request_id: str
    type: Literal["classification"] = "classification"
    algorithm: AlgorithmIdentity
    input: ImageMetadata
    timing: TimingMetadata
    data: ClassificationData


class Segment(ResultModel):
    label: str = Field(min_length=1, max_length=128)
    score: float = Field(ge=0, le=1)
    mask_uri: str = Field(min_length=1, max_length=5_600_000)

    @field_validator("mask_uri")
    @classmethod
    def validate_mask_uri(cls, value: str) -> str:
        if value.startswith("data:image/png;base64,"):
            return value
        raise ValueError("mask_uri must be a PNG data URI")


class SegmentationData(ResultModel):
    segments: list[Segment] = Field(default_factory=list, max_length=16)


class SegmentationResult(ResultModel):
    schema_version: Literal["1.0"] = "1.0"
    request_id: str
    type: Literal["segmentation"] = "segmentation"
    algorithm: AlgorithmIdentity
    input: ImageMetadata
    timing: TimingMetadata
    data: SegmentationData


class Polygon(RootModel[list[list[float]]]):
    @model_validator(mode="after")
    def validate_polygon(self) -> "Polygon":
        if len(self.root) < 3 or any(len(point) != 2 for point in self.root):
            raise ValueError("polygon must contain at least three [x, y] points")
        if any(coordinate < 0 for point in self.root for coordinate in point):
            raise ValueError("polygon coordinates cannot be negative")
        return self


class OcrText(ResultModel):
    text: str
    score: float = Field(ge=0, le=1)
    polygon: Polygon


class OcrData(ResultModel):
    texts: list[OcrText] = Field(default_factory=list, max_length=10_000)


class OcrResult(ResultModel):
    schema_version: Literal["1.0"] = "1.0"
    request_id: str
    type: Literal["ocr"] = "ocr"
    algorithm: AlgorithmIdentity
    input: ImageMetadata
    timing: TimingMetadata
    data: OcrData


class Keypoint(ResultModel):
    name: str = Field(min_length=1, max_length=128)
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    score: float = Field(ge=0, le=1)


class PoseInstance(ResultModel):
    score: float = Field(ge=0, le=1)
    keypoints: list[Keypoint] = Field(default_factory=list, max_length=1_000)


class PoseData(ResultModel):
    instances: list[PoseInstance] = Field(default_factory=list, max_length=1_000)


class PoseEstimationResult(ResultModel):
    schema_version: Literal["1.0"] = "1.0"
    request_id: str
    type: Literal["pose_estimation"] = "pose_estimation"
    algorithm: AlgorithmIdentity
    input: ImageMetadata
    timing: TimingMetadata
    data: PoseData


AlgorithmResult = Annotated[
    ObjectDetectionResult
    | ClassificationResult
    | SegmentationResult
    | OcrResult
    | PoseEstimationResult,
    Field(discriminator="type"),
]
