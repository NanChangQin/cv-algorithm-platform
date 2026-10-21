from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from ...core.request_context import project_id_from
from ...dependencies import get_container
from .service import DeploymentBackend, DeploymentManifestService

router = APIRouter(prefix="/deployment-manifests", tags=["deployments"])


class DeploymentManifestResponse(BaseModel):
    algorithm_version_id: UUID
    backend: DeploymentBackend
    files: dict[str, str]


@router.get("/{algorithm_id}", response_model=DeploymentManifestResponse)
def generate_manifest(
    algorithm_id: UUID,
    backend: DeploymentBackend,
    request: Request,
    container=Depends(get_container),
) -> DeploymentManifestResponse:
    service = DeploymentManifestService(container.algorithms, project_id_from(request))
    return DeploymentManifestResponse(
        algorithm_version_id=algorithm_id,
        backend=backend,
        files=service.generate(algorithm_id, backend),
    )
