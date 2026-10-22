import stat
from io import BytesIO
from pathlib import Path

from PIL import Image

from cv_platform.modules.assets.infrastructure.local_storage import LocalAssetStorage


def test_stored_asset_is_readable_by_algorithm_container(tmp_path: Path) -> None:
    content = BytesIO()
    Image.new("RGB", (8, 6), color="blue").save(content, format="JPEG")
    content.seek(0)
    storage = LocalAssetStorage(tmp_path, "/data", 1024 * 1024, 1_000_000)

    stored = storage.store_image(content, "sample.jpg", "image/jpeg")

    mode = storage.resolve(stored.storage_key).stat().st_mode
    assert mode & stat.S_IROTH
