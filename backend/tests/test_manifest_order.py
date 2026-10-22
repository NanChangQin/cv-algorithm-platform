import shutil
from dataclasses import replace
from pathlib import Path
from uuid import UUID

from cv_algorithm_sdk import AlgorithmManifest
from fastapi.testclient import TestClient

from cv_platform.dependencies import get_container
from cv_platform.modules.algorithms.application.manifest_order import restore_manifest_order

ORIGINAL = ["confidence", "max_results", "use_half", "mode"]
# JSONB 先按键的字节长度、再按字节排序
SCRAMBLED = ["mode", "use_half", "confidence", "max_results"]


def jsonb_order(value):
    if isinstance(value, dict):
        keys = sorted(value, key=lambda key: (len(key.encode()), key.encode()))
        return {key: jsonb_order(value[key]) for key in keys}
    if isinstance(value, list):
        return [jsonb_order(item) for item in value]
    return value


def import_template(client: TestClient) -> UUID:
    template = client.get("/api/v1/algorithms/template?task_type=object_detection")
    response = client.post(
        "/api/v1/algorithms/import",
        files={"package": ("template.zip", template.content, "application/zip")},
    )
    assert response.status_code == 201
    return UUID(response.json()["id"])


def scramble(algorithm_id: UUID) -> None:
    """模拟迁移 0003 之前存进 JSONB、再读回来的 manifest。"""
    repository = get_container().algorithms
    algorithm = repository.get(algorithm_id)
    dumped = jsonb_order(algorithm.manifest.model_dump(mode="json"))
    repository.save(replace(algorithm, manifest=AlgorithmManifest.model_validate(dumped)))


def parameter_order(client: TestClient, algorithm_id: UUID) -> list[str]:
    listed = client.get("/api/v1/algorithms").json()
    return list(next(item for item in listed if item["id"] == str(algorithm_id))["parameters"])


def test_parameter_order_of_imported_packages_is_restored(client: TestClient) -> None:
    algorithm_id = import_template(client)
    assert parameter_order(client, algorithm_id) == ORIGINAL
    scramble(algorithm_id)
    assert parameter_order(client, algorithm_id) == SCRAMBLED

    assert restore_manifest_order(get_container().algorithms) == 1
    assert parameter_order(client, algorithm_id) == ORIGINAL
    assert restore_manifest_order(get_container().algorithms) == 0


def test_changed_or_missing_packages_are_left_alone(client: TestClient) -> None:
    algorithm_id = import_template(client)
    scramble(algorithm_id)
    package = Path(get_container().algorithms.get(algorithm_id).package_path)
    manifest = package / "manifest.yaml"
    manifest.write_text(manifest.read_text().replace("default: 0.5", "default: 0.6"))
    assert restore_manifest_order(get_container().algorithms) == 0

    shutil.rmtree(package)
    assert restore_manifest_order(get_container().algorithms) == 0
    assert parameter_order(client, algorithm_id) == SCRAMBLED
