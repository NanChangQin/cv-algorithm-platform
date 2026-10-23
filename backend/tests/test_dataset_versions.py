import hashlib
import io
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
import yaml
from test_training import create_run, make_zip, ultralytics_layout, upload_dataset

from cv_platform.core.errors import ApplicationError
from cv_platform.dependencies import get_container


def flat_entries(count=30):
    entries = {"data.yaml": "names: [cat, dog]"}
    for i in range(count):
        entries[f"images/{i:03}.jpg"] = f"image-{i}"
        entries[f"labels/{i:03}.txt"] = f"{i % 2} 0.5 0.5 0.2 0.4\n"
    return entries


def import_with(client, entries, config=None, parent=None):
    data = {"split_config": json.dumps(config or {})}
    if parent:
        data["parent_id"] = parent
    return client.post("/api/v1/training/datasets", data=data,
                       files={"file": ("dataset.zip", make_zip(entries), "application/zip")})


def test_test_is_retained_and_never_used_as_implicit_validation(client):
    entries = flat_entries(10)
    entries = {key.replace("images/", "images/train/").replace("labels/", "labels/train/"):
               value for key, value in entries.items()}
    entries.update({"images/test/holdout.jpg": "holdout",
                    "labels/test/holdout.txt": "1 0.5 0.5 0.3 0.3"})
    response = import_with(client, entries)
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["stats"]["splits"]["test"]["images"] == 1
    assert data["stats"]["splits"]["val"]["images"] == 2
    storage = get_container().training_storage
    with zipfile.ZipFile(storage.path(data["id"])) as archive:
        assert "images/test/holdout.jpg" in archive.namelist()
        assert "labels/test/holdout.txt" in archive.namelist()
        assert yaml.safe_load(archive.read("data.yaml"))["test"] == "images/test"
        assert not any("holdout" in n for n in archive.namelist() if "/val/" in n)


def test_seed_and_hash_ignore_zip_order_timestamp_and_wrapper(client):
    entries = flat_entries()
    config = {"mode": "reshuffle", "train": .6, "val": .2, "test": .2, "seed": 42}
    first = import_with(client, entries, config).json()
    second = import_with(client, {f"wrapped/{key}": value for key, value in
                                  reversed(list(entries.items()))}, config).json()
    assert first["sha256"] == second["sha256"]
    assert first["source_sha256"] != second["source_sha256"]
    assert first["stats"]["splits"] == second["stats"]["splits"]
    assert {s: v["images"] for s, v in first["stats"]["splits"].items()} == {
        "train": 18, "val": 6, "test": 6,
    }
    changed = import_with(client, entries, {**config, "seed": 43}).json()
    assert first["sha256"] != changed["sha256"]


def test_versions_are_append_only_and_keep_training_snapshot(client, node):
    first = import_with(client, flat_entries()).json()
    base = f"/api/v1/training/datasets/{first['id']}"
    original = client.get(f"{base}/archive").content
    assert hashlib.sha256(original).hexdigest() == first["sha256"]
    run = create_run(client, first["id"])
    config = {"mode": "reshuffle", "train": .6, "val": .2, "test": .2, "seed": 99}
    response = client.post(f"{base}/versions", json=config)
    assert response.status_code == 201, response.text
    second = response.json()
    assert second["family_id"] == first["family_id"] == first["id"]
    assert second["parent_id"] == first["id"]
    assert second["version"] == 2 and first["version"] == 1
    assert second["source_sha256"] == first["source_sha256"]
    assert client.get(f"{base}/archive").content == original
    assert client.patch(base, json={"sha256": "change"}).status_code == 405
    assert client.delete(base).status_code == 409
    assert client.get(f"/api/v1/training/runs/{run['id']}").json()[
        "progress"]["dataset"]["sha256"] == first["sha256"]
    # 节点进度不会覆盖平台记录的数据来源。
    orchestrator = get_container().training_orchestrator
    orchestrator.prepare(UUID(run["id"]))
    node.state = {"state": "completed", "progress": {"epoch": 1}, "files": {}}
    orchestrator.sync(UUID(run["id"]))
    assert client.get(f"/api/v1/training/runs/{run['id']}").json()[
        "progress"]["dataset"]["version"] == 1
    # 删除最新版本后也不复用编号。
    assert client.delete(f"/api/v1/training/datasets/{second['id']}").status_code == 204
    third = client.post(f"{base}/versions", json=config).json()
    assert third["version"] == 3


def test_concurrent_version_numbers_are_unique(client):
    first = upload_dataset(client, ultralytics_layout())
    repository = get_container().training
    parent = repository.get_dataset(UUID(first["id"]))

    def add(_):
        return repository.add_dataset(replace(parent, id=uuid4(), parent_id=parent.id))

    with ThreadPoolExecutor(max_workers=4) as pool:
        versions = list(pool.map(add, range(8)))
    assert sorted(v.version for v in versions) == list(range(2, 10))
    assert repository.get_dataset(parent.id) == parent
    with pytest.raises(ApplicationError, match="引用"):
        repository.delete_dataset(parent.id)


def test_duplicates_leakage_and_preview_report(client):
    entries = ultralytics_layout()
    entries["images/test/duplicate.jpg"] = entries["images/train/a.jpg"]
    entries["labels/test/duplicate.txt"] = "0 0.5 0.5 0.2 0.2"
    data = upload_dataset(client, entries)
    assert data["stats"]["duplicate_groups"] == 1
    assert data["stats"]["leakage_groups"] == 1
    base = f"/api/v1/training/datasets/{data['id']}"
    page = client.get(f"{base}/images?split=test&limit=1").json()
    assert page["total"] == 1
    image = page["items"][0]
    assert image["boxes"] == [{"class_id": 0, "cx": .5, "cy": .5, "width": .2, "height": .2}]
    preview = client.get(f"{base}/images/{image['id']}/preview")
    assert preview.status_code == 200 and preview.headers["content-type"] == "image/jpeg"
    assert preview.content.startswith(b"\xff\xd8")
    assert client.get(f"{base}/images/-1/preview").status_code == 404
    assert client.get(f"{base}/images/999/preview").status_code == 404
    assert client.get(f"{base}/images?offset=-1").status_code == 422
    assert client.get(f"{base}/images?limit=1000").status_code == 422
    report = client.get(f"{base}/report").json()
    assert len(report["index"]["leakage"]) == 1
    assert len(report["index"]["images"]) == 4
    assert hashlib.sha256(client.get(f"{base}/archive?source=true").content).hexdigest() == (
        data["source_sha256"])
    reshuffled = client.post(f"{base}/versions", json={"mode": "reshuffle"}).json()
    assert reshuffled["stats"]["duplicate_groups"] == 1
    assert reshuffled["stats"]["leakage_groups"] == 0


@pytest.mark.parametrize("config", [
    {"train": .8, "val": .3}, {"test": -.2}, {"seed": True}, {"seed": 1.5},
    {"mode": "random"}, {"unexpected": 3}, {"val": 0}, {"seed": -1},
])
def test_bad_split_config_is_rejected(client, config):
    assert import_with(client, flat_entries(), config).status_code == 422


def test_strict_repair_and_corrupt_images(client):
    entries = ultralytics_layout()
    entries["labels/train/a.txt"] += "bad label\n"
    strict = import_with(client, entries, {"validation": "strict"})
    assert strict.status_code == 400 and "严格" in strict.json()["message"]
    data = import_with(client, entries).json()
    report = client.get(f"/api/v1/training/datasets/{data['id']}/report").json()
    assert len(report["index"]["invalid_labels"]) == 1
    entries["images/train/a.jpg"] = b"not an image"
    corrupt = import_with(client, entries)
    assert corrupt.status_code == 400 and "损坏" in corrupt.json()["message"]


def test_duplicate_zip_paths_and_immutable_archive(client):
    entries = ultralytics_layout()
    entries["images/train/A.JPG"] = "duplicate-name"
    duplicate = import_with(client, entries)
    assert duplicate.status_code == 400 and "重复路径" in duplicate.json()["message"]
    data = upload_dataset(client, ultralytics_layout())
    storage = get_container().training_storage
    with pytest.raises(ApplicationError, match="覆盖"):
        storage.import_archive(
            io.BytesIO(make_zip(ultralytics_layout())), "dataset.zip", data["id"],
        )


def test_all_dataset_endpoints_are_project_scoped(client):
    from cv_platform.modules.training.domain import TrainingDataset

    data = upload_dataset(client, ultralytics_layout())
    repository = get_container().training
    project = client.post("/api/v1/projects", json={"name": "other"}).json()
    # 另一个项目的数据，即使 UUID 已知也不允许读取和派生版本。
    foreign = TrainingDataset.create(UUID(project["id"]), "other", ("cat",), {}, 0, "a"*64,
                                     "admin")
    foreign = repository.add_dataset(foreign)
    base = f"/api/v1/training/datasets/{foreign.id}"
    for suffix in ("", "/images", "/images/0/preview", "/archive", "/archive?source=true",
                   "/report"):
        assert client.get(base + suffix).status_code == 404
    assert client.post(base + "/versions", json={}).status_code == 404
    assert import_with(client, flat_entries(), parent=str(foreign.id)).status_code == 404
    assert data["id"] != str(foreign.id)


def test_legacy_dataset_preview_does_not_rewrite_archive(client):
    data = upload_dataset(client, ultralytics_layout())
    storage = get_container().training_storage
    storage.index_path(data["id"]).unlink()
    storage.source_path(data["id"]).unlink()
    base = f"/api/v1/training/datasets/{data['id']}"
    page = client.get(base + "/images").json()
    assert page["total"] == 3
    assert client.get(base + "/images/0/preview").status_code == 200
    assert hashlib.sha256(storage.path(data["id"]).read_bytes()).hexdigest() == data["sha256"]
    assert client.get(base + "/archive?source=true").status_code == 404
    assert client.post(base + "/versions", json={}).status_code == 404


def test_empty_distinct_pool_rejects_instead_of_leaking(client):
    entries = flat_entries(4)
    for key in entries:
        if key.endswith(".jpg"):
            entries[key] = "identical"
    response = import_with(client, entries)
    assert response.status_code == 400 and "不同内容" in response.json()["message"]
