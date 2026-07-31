from typing import Optional

from pydantic import BaseModel


class ArtifactMigrateRequest(BaseModel):
    artifact_id: str


class ArtifactRef(BaseModel):
    id: str
    project_id: str
    artifact_id: str
    artifact_name: str
    artifact_type: str
    artifact_kind: Optional[str] = None
    content_summary: Optional[str] = None
    migrated_at: str
    source_session_id: Optional[str] = None

    @classmethod
    def from_row(cls, row: dict) -> "ArtifactRef":
        return cls(
            id=row["id"],
            project_id=row["project_id"],
            artifact_id=row["artifact_id"],
            artifact_name=row["artifact_name"],
            artifact_type=row["artifact_type"],
            artifact_kind=row.get("artifact_kind"),
            content_summary=row.get("content_summary"),
            migrated_at=row["migrated_at"],
            source_session_id=row.get("source_session_id"),
        )
