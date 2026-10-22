from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ...dependencies import get_container
from .cluster_gateway import RuntimeNode

router = APIRouter(prefix="/runtime-nodes", tags=["system"])


class RegisterNodeRequest(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    name: str = Field(min_length=1, max_length=128)
    manager_url: str = Field(min_length=8, max_length=2048)
    enabled: bool = True


class RuntimeNodeResponse(RegisterNodeRequest):
    pass


@router.post("", response_model=RuntimeNodeResponse, status_code=201)
def register_node(payload: RegisterNodeRequest, container=Depends(get_container)):
    return container.algorithm_manager.register_node(RuntimeNode(**payload.model_dump()))


@router.get("", response_model=list[RuntimeNodeResponse])
def list_nodes(container=Depends(get_container)):
    return container.algorithm_manager.list_nodes()
