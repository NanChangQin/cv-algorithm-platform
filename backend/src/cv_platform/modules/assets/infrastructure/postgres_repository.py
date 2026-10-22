from __future__ import annotations

from uuid import UUID

from ....core.database import Database
from ..domain.entities import ImageAsset
from ..domain.repositories import AssetQuery


def _where(criteria: AssetQuery) -> tuple[str, list[object]]:
    clauses = ["project_id = %s", "deleted_at IS NULL"]
    params: list[object] = [criteria.project_id]
    if criteria.text:
        escaped = criteria.text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clauses.append("original_name ILIKE %s")
        params.append(f"%{escaped}%")
    if criteria.ids:
        clauses.append("id = ANY(%s)")
        params.append(list(criteria.ids))
    return "WHERE " + " AND ".join(clauses), params


class PostgresAssetRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    @staticmethod
    def _from_row(row: dict[str, object]) -> ImageAsset:
        return ImageAsset(
            id=row["id"],
            original_name=str(row["original_name"]),
            storage_key=str(row["storage_key"]),
            algorithm_uri=str(row["algorithm_uri"]),
            sha256=str(row["sha256"]),
            media_type=str(row["media_type"]),
            width=int(row["width"]),
            height=int(row["height"]),
            size_bytes=int(row["size_bytes"]),
            created_at=row["created_at"],
            owner_id=str(row["owner_label"]),
            project_id=row["project_id"],
        )

    def add(self, asset: ImageAsset) -> None:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO image_assets (
                    id, project_id, owner_id, owner_label, original_name, storage_key,
                    algorithm_uri, sha256, media_type, width, height, size_bytes, created_at
                ) VALUES (%s, %s, NULL, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    asset.id,
                    asset.project_id,
                    asset.owner_id,
                    asset.original_name,
                    asset.storage_key,
                    asset.algorithm_uri,
                    asset.sha256,
                    asset.media_type,
                    asset.width,
                    asset.height,
                    asset.size_bytes,
                    asset.created_at,
                ),
            )

    def list(self) -> list[ImageAsset]:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM image_assets WHERE deleted_at IS NULL ORDER BY created_at DESC"
            )
            return [self._from_row(row) for row in cursor.fetchall()]

    def get(self, asset_id: UUID) -> ImageAsset | None:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM image_assets WHERE id = %s AND deleted_at IS NULL",
                (asset_id,),
            )
            row = cursor.fetchone()
            return self._from_row(row) if row else None

    def query(self, criteria: AssetQuery) -> list[ImageAsset]:
        where, params = _where(criteria)
        sql = f"SELECT * FROM image_assets {where} ORDER BY created_at DESC, id DESC"
        if criteria.limit is not None:
            sql += " LIMIT %s"
            params.append(criteria.limit)
        if criteria.offset:
            sql += " OFFSET %s"
            params.append(criteria.offset)
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, params)
            return [self._from_row(row) for row in cursor.fetchall()]

    def count(self, criteria: AssetQuery) -> int:
        where, params = _where(criteria)
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(f"SELECT count(*) AS total FROM image_assets {where}", params)
            row = cursor.fetchone()
            return int(row["total"]) if row else 0

    def find_by_sha256(self, project_id: UUID, sha256: str) -> ImageAsset | None:
        return self._find_one("sha256", project_id, sha256)

    def find_by_algorithm_uri(self, project_id: UUID, algorithm_uri: str) -> ImageAsset | None:
        return self._find_one("algorithm_uri", project_id, algorithm_uri)

    def _find_one(self, column: str, project_id: UUID, value: str) -> ImageAsset | None:
        with self._database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT * FROM image_assets
                WHERE project_id = %s AND {column} = %s AND deleted_at IS NULL
                ORDER BY created_at DESC LIMIT 1
                """,
                (project_id, value),
            )
            row = cursor.fetchone()
            return self._from_row(row) if row else None
