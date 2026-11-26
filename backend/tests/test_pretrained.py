from uuid import uuid4

import pytest

from cv_platform.modules.algorithms.api.schemas import AlgorithmResponse
from cv_platform.modules.algorithms.infrastructure.memory_repository import (
    InMemoryAlgorithmRepository,
)
from cv_platform.modules.algorithms.infrastructure.model_store import ModelStore
from cv_platform.modules.algorithms.pretrained import install


def test_install_is_idempotent_scoped_and_transfers_weights(tmp_path):
    weights = tmp_path / "weights"
    weights.mkdir()
    for scale in "nsmlx":
        (weights / f"yolo26{scale}.pt").write_bytes(f"fixture-{scale}".encode())
    store, repo, project = ModelStore(tmp_path / "store"), InMemoryAlgorithmRepository(), uuid4()
    models = install(repo, store, weights, "yolo:dev", project)
    assert [a.manifest.id for a in models] == [f"yolo26{s}" for s in "nsmlx"]
    assert [a.id for a in install(repo, store, weights, "yolo:dev", project)] == [
        a.id for a in models
    ]
    for model in models:
        response = AlgorithmResponse.from_entity(model)
        assert response.lineage is None
        assert response.pretrained["base_model"] == f"{model.manifest.id}.pt"
        assert len(store.files(model)) == 2
        assert model.project_id == project
    others = install(repo, store, weights, "yolo:dev", uuid4())
    assert {a.id for a in others}.isdisjoint({a.id for a in models})
    (weights / "yolo26n.pt").write_bytes(b"changed")
    with pytest.raises(ValueError, match="内容不一致"):
        install(repo, store, weights, "yolo:dev", project)
    assert (store.directory(models[0].id) / "model.pt").read_bytes() == b"fixture-n"


def test_missing_weights_does_not_install_partial_catalog(tmp_path):
    repo = InMemoryAlgorithmRepository()
    with pytest.raises(ValueError, match="缺少权重"):
        install(repo, ModelStore(tmp_path / "store"), tmp_path, "yolo:dev", uuid4())
    assert not repo.list()
