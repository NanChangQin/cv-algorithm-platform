from uuid import UUID
import io
import zipfile

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse

from ....core.container import ApplicationContainer
from ....dependencies import get_container
from ....core.request_context import project_id_from
from ..application.service import AssetService
from .schemas import AssetResponse, BatchUploadResponse

router = APIRouter(prefix="/assets", tags=["assets"])


def get_service(
    request: Request,
    container: ApplicationContainer = Depends(get_container),
) -> AssetService:
    return AssetService(
        container.assets,
        container.asset_storage,
        project_id_from(request),
        request.state.session.username,
    )


@router.post("/upload", response_model=BatchUploadResponse, status_code=status.HTTP_201_CREATED)
def upload_assets(
    files: list[UploadFile] = File(...),
    service: AssetService = Depends(get_service),
) -> BatchUploadResponse:
    assets = [
        service.upload(file.file, file.filename or "", file.content_type)
        for file in files
    ]
    return BatchUploadResponse(assets=[AssetResponse.model_validate(item) for item in assets])


@router.get("", response_model=list[AssetResponse])
def list_assets(service: AssetService = Depends(get_service)) -> list[AssetResponse]:
    return [AssetResponse.model_validate(item) for item in service.list_assets()]


@router.get("/{asset_id}", response_model=AssetResponse)
def get_asset(asset_id: UUID, service: AssetService = Depends(get_service)) -> AssetResponse:
    return AssetResponse.model_validate(service.get_asset(asset_id))


@router.get("/{asset_id}/content", response_class=FileResponse)
def get_asset_content(
    asset_id: UUID,
    request: Request,
    container: ApplicationContainer = Depends(get_container),
) -> FileResponse:
    asset = AssetService(
        container.assets,
        container.asset_storage,
        project_id_from(request),
    ).get_asset(asset_id)
    path = container.asset_storage.resolve(asset.storage_key)
    return FileResponse(path, media_type=asset.media_type, filename=asset.original_name)


THUMBNAIL_SIZES = {160, 320, 640}


@router.get("/{asset_id}/thumbnail", response_class=FileResponse)
def get_asset_thumbnail(
    asset_id: UUID,
    request: Request,
    size: int = Query(default=320),
    container: ApplicationContainer = Depends(get_container),
) -> FileResponse:
    if size not in THUMBNAIL_SIZES:
        from ....core.errors import ApplicationError

        raise ApplicationError("THUMBNAIL_SIZE_INVALID", "缩略图尺寸只支持 160、320、640")
    asset = AssetService(
        container.assets,
        container.asset_storage,
        project_id_from(request),
    ).get_asset(asset_id)
    path = container.asset_storage.thumbnail(asset.storage_key, size)
    # 缩略图文件名即原图内容哈希，同一资源的缩略图不会变化
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=604800, immutable"},
    )


@router.post("/download", response_class=StreamingResponse)
def download_assets(
    asset_ids: list[UUID],
    request: Request,
    container: ApplicationContainer = Depends(get_container),
) -> StreamingResponse:
    if not asset_ids or len(asset_ids) > 100:
        from ....core.errors import ApplicationError

        raise ApplicationError("DOWNLOAD_INVALID", "请选择 1 到 100 个图片资源")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        used_names: set[str] = set()
        service = AssetService(
            container.assets,
            container.asset_storage,
            project_id_from(request),
        )
        for asset_id in asset_ids:
            asset = service.get_asset(asset_id)
            name = asset.original_name
            if name in used_names:
                name = f"{asset.id.hex[:8]}-{name}"
            used_names.add(name)
            archive.write(container.asset_storage.resolve(asset.storage_key), arcname=name)
    output.seek(0)
    return StreamingResponse(
        output,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="cv-assets.zip"'},
    )
