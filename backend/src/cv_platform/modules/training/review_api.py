from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, field_validator

from ...core.container import ApplicationContainer
from ...core.errors import ApplicationError
from ...core.request_context import project_id_from
from ...dependencies import get_container
from .api import DatasetResponse, get_service
from .feedback import DatasetFeedbackRequest, FeedbackSample, feedback_to_dataset
from .service import TrainingService

router = APIRouter(prefix="/training/reviews", tags=["sample reviews"])


class CollectRequest(BaseModel):
    task_id: UUID
    evaluation_id: UUID | None = None


class Box(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=64)
    bbox: tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat]

    @field_validator("label")
    @classmethod
    def label_not_blank(cls, value):
        if not value.strip():
            raise ValueError("类别名称不能为空")
        return value.strip()


class SaveReviewRequest(BaseModel):
    version: int = Field(ge=1)
    boxes: list[Box] = Field(max_length=2000)
    reviewed: bool = False
    note: str = Field(default="", max_length=2000)


class PublishReviewsRequest(BaseModel):
    dataset_id: UUID
    review_ids: list[UUID] = Field(min_length=1, max_length=200)
    target_split: Literal["train", "val", "test"] = "train"
    acknowledge_evaluation_reuse: bool = False


@router.get("")
def list_reviews(
    request: Request,
    status: Literal["all", "draft", "reviewed", "published"] = "all",
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=24, ge=1, le=100),
    container: ApplicationContainer = Depends(get_container),
):
    items = container.reviews.list(project_id_from(request))
    items = [r for r in items if status == "all" or r["status"] == status]
    return {"items": items[offset : offset + limit], "total": len(items)}


@router.post("", status_code=201)
def collect(
    payload: CollectRequest,
    request: Request,
    container: ApplicationContainer = Depends(get_container),
):
    project = project_id_from(request)
    task = container.tasks.get(payload.task_id)
    if not task or task.project_id != project:
        raise ApplicationError("TASK_NOT_FOUND", "任务不存在", 404)
    if not task.result or task.result.type != "object_detection" or not task.asset_id:
        raise ApplicationError("REVIEW_UNSUPPORTED", "仅有检测结果和原图的任务可收集", 409)
    asset = container.assets.get(task.asset_id)
    if not asset or asset.project_id != project:
        raise ApplicationError("ASSET_NOT_FOUND", "原图已不可用", 404)
    truth, source = None, {}
    if payload.evaluation_id is None:
        matched = next(
            (
                r
                for r in container.evaluations.list_runs(project)
                if str(task.id) in r.task_ids.values()
            ),
            None,
        )
        if matched:
            payload.evaluation_id = matched.id
    if payload.evaluation_id:
        evaluation = container.evaluations.get_run(payload.evaluation_id)
        if (
            not evaluation
            or evaluation.project_id != project
            or str(task.id) not in evaluation.task_ids.values()
        ):
            raise ApplicationError("EVALUATION_NOT_FOUND", "此任务不属于该评测", 404)
        dataset = container.evaluations.get_dataset(evaluation.dataset_id)
        if not dataset or dataset.project_id != project:
            raise ApplicationError("DATASET_NOT_FOUND", "评测集已不存在", 404)
        item = next(i for i in dataset.items if i.asset_id == asset.id)
        truth = item.ground_truth.get("detections", [])
        source = {k: v for k, v in item.ground_truth.items() if k.startswith("source_")}
    predictions = [
        {"label": d.label, "bbox": list(d.bbox.root)} for d in task.result.data.detections
    ]
    return container.reviews.create(
        project,
        task.id,
        {
            "asset_id": str(asset.id),
            "name": asset.original_name,
            "width": asset.width,
            "height": asset.height,
            "asset_sha256": asset.sha256,
            "evaluation_id": str(payload.evaluation_id) if payload.evaluation_id else None,
            "algorithm_version_id": str(task.algorithm_version_id),
            "source": source,
            "boxes": truth if truth is not None else predictions,
            "predictions": predictions,
            "ground_truth": truth,
            "note": "",
            "created_by": request.state.session.username,
        },
    )


@router.get("/{review_id}")
def get_review(
    review_id: UUID, request: Request, container: ApplicationContainer = Depends(get_container)
):
    return container.reviews.get(review_id, project_id_from(request))


@router.put("/{review_id}")
def save_review(
    review_id: UUID,
    payload: SaveReviewRequest,
    request: Request,
    container: ApplicationContainer = Depends(get_container),
):
    project = project_id_from(request)
    current = container.reviews.get(review_id, project)
    for box in payload.boxes:
        x1, y1, x2, y2 = box.bbox
        if not (0 <= x1 < x2 <= current["width"] and 0 <= y1 < y2 <= current["height"]):
            raise ApplicationError("REVIEW_BOX_INVALID", "检测框须在图片范围内且宽高大于 0")
    return container.reviews.update(
        review_id,
        project,
        payload.version,
        {
            "boxes": [b.model_dump(mode="json") for b in payload.boxes],
            "note": payload.note,
            "status": "reviewed" if payload.reviewed else "draft",
            "reviewed_by": request.state.session.username if payload.reviewed else None,
        },
    )


@router.post("/publish", response_model=DatasetResponse, status_code=201)
def publish(
    payload: PublishReviewsRequest,
    request: Request,
    training: TrainingService = Depends(get_service),
    container: ApplicationContainer = Depends(get_container),
):
    project = project_id_from(request)
    with container.reviews.lock:
        rows = [container.reviews.get(key, project) for key in dict.fromkeys(payload.review_ids)]
        if any(r["status"] != "reviewed" for r in rows):
            raise ApplicationError("REVIEW_REQUIRED", "仅人工审核通过且未发布的样本可生成版本", 409)
        if len({r["asset_id"] for r in rows}) != len(rows):
            raise ApplicationError("REVIEW_DUPLICATE", "同一图片有多份审核结果，请只选择其中一份")
        if any(r["evaluation_id"] for r in rows) and not payload.acknowledge_evaluation_reuse:
            raise ApplicationError(
                "REVIEW_EVALUATION_REUSE", "请确认：用评测样本改进模型后，不再将其作为独立验收数据"
            )
        result = feedback_to_dataset(
            training,
            payload.dataset_id,
            DatasetFeedbackRequest(
                samples=[
                    FeedbackSample(task_id=UUID(r["task_id"]), boxes=r["boxes"]) for r in rows
                ],
                target_split=payload.target_split,
            ),
            tasks=container.tasks,
            assets=container.assets,
            asset_storage=container.asset_storage,
            project_id=project,
        )
        for row in rows:
            container.reviews.update(
                UUID(row["id"]),
                project,
                row["version"],
                {
                    "status": "published",
                    "publications": [
                        *row["publications"],
                        {
                            "dataset_id": str(result.id),
                            "version": result.version,
                            "review_version": row["version"],
                            "boxes": row["boxes"],
                            "reviewed_by": row.get("reviewed_by"),
                        },
                    ],
                },
            )
        return DatasetResponse.of(result)
