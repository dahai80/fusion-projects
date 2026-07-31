import io
import logging
import zipfile
from typing import Optional

import httpx

from project_service.models.artifact_ref import ArtifactRef
from project_service.models.project import (
    Project,
    ProjectCreate,
    ProjectListItem,
    ProjectUpdate,
)
from project_service.store.file_store import FileStore
from project_service.store.project_store import ProjectStore

logger = logging.getLogger(__name__)

ARTIFACTS_ENGINE_URL = "http://127.0.0.1:8892"

_TYPE_EXTENSIONS = {
    "html": ".html",
    "react": ".jsx",
    "code": ".py",
    "markdown": ".md",
    "data": ".json",
}


def _type_extension(artifact_type: str) -> str:
    return _TYPE_EXTENSIONS.get(artifact_type, ".txt")


class ProjectError(Exception):
    pass


class ProjectNotFound(ProjectError):
    pass


class ProjectNotArchived(ProjectError):
    pass


class ArtifactAlreadyMigrated(ProjectError):
    pass


class ArtifactNotFound(ProjectError):
    pass


class ProjectManager:
    def __init__(
        self,
        store: Optional[ProjectStore] = None,
        file_store: Optional[FileStore] = None,
    ) -> None:
        self.store = store or ProjectStore()
        self.file_store = file_store or FileStore()

    async def create(self, payload: ProjectCreate) -> Project:
        data = payload.model_dump()
        row = self.store.create_project(data)
        self.file_store.init_project(row["id"])
        logger.info("project created id=%s name=%s", row["id"], row["name"])
        return Project.from_row(row)

    async def get(self, project_id: str) -> Project:
        row = self.store.get_project(project_id)
        if not row:
            raise ProjectNotFound(project_id)
        return Project.from_row(row)

    async def list(
        self,
        include_archived: bool = False,
        only_starred: bool = False,
    ) -> list[ProjectListItem]:
        rows = self.store.list_projects(
            include_archived=include_archived,
            only_starred=only_starred,
        )
        return [ProjectListItem.from_row(r) for r in rows]

    async def update(self, project_id: str, payload: ProjectUpdate) -> Project:
        fields = payload.model_dump(exclude_unset=True)
        row = self.store.update_project(project_id, fields)
        if not row:
            raise ProjectNotFound(project_id)
        logger.info("project updated id=%s fields=%s", project_id, list(fields.keys()))
        return Project.from_row(row)

    async def archive(self, project_id: str) -> Project:
        row = self.store.set_archived(project_id, True)
        if not row:
            raise ProjectNotFound(project_id)
        return Project.from_row(row)

    async def unarchive(self, project_id: str) -> Project:
        row = self.store.set_archived(project_id, False)
        if not row:
            raise ProjectNotFound(project_id)
        return Project.from_row(row)

    async def star(self, project_id: str, starred: bool = True) -> Project:
        row = self.store.set_starred(project_id, starred)
        if not row:
            raise ProjectNotFound(project_id)
        return Project.from_row(row)

    async def delete(self, project_id: str) -> None:
        row = self.store.get_project(project_id)
        if not row:
            raise ProjectNotFound(project_id)
        if not row["is_archived"]:
            raise ProjectNotArchived(
                "project must be archived before delete: " + project_id
            )
        artifact_rows = self.store.list_artifact_refs(project_id)
        for ar in artifact_rows:
            try:
                await self._call_artifacts_engine(
                    "artifact.update",
                    {"artifact_id": ar["artifact_id"], "in_project_kb": False},
                )
            except Exception:
                logger.warning("failed to clear in_project_kb for artifact=%s", ar["artifact_id"])
        self.store.delete_project(project_id)
        self.file_store.remove_project(project_id)
        logger.info("project deleted id=%s artifacts_cleared=%d", project_id, len(artifact_rows))

    async def _call_artifacts_engine(self, method: str, params: dict) -> dict:
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": 1,
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(ARTIFACTS_ENGINE_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()
        if "error" in data:
            raise ProjectError(f"artifacts-engine error: {data['error']}")
        return data.get("result", {})

    async def migrate_artifact(
        self,
        project_id: str,
        artifact_id: str,
    ) -> ArtifactRef:
        await self.get(project_id)
        existing = self.store.get_artifact_ref(artifact_id)
        if existing:
            raise ArtifactAlreadyMigrated(
                f"artifact {artifact_id} already migrated to project {existing['project_id']}"
            )
        result = await self._call_artifacts_engine(
            "artifact.get", {"artifact_id": artifact_id}
        )
        artifact = result.get("artifact", {})
        await self._call_artifacts_engine(
            "artifact.move_to_project_kb", {"artifact_id": artifact_id}
        )
        ref_data = {
            "project_id": project_id,
            "artifact_id": artifact_id,
            "artifact_name": artifact.get("name", ""),
            "artifact_type": artifact.get("type", ""),
            "artifact_kind": artifact.get("kind"),
            "content_summary": artifact.get("summary"),
            "source_session_id": artifact.get("session_id"),
        }
        row = self.store.create_artifact_ref(ref_data)
        logger.info("artifact migrated artifact=%s project=%s", artifact_id, project_id)
        return ArtifactRef.from_row(row)

    async def list_artifacts(
        self,
        project_id: str,
        artifact_type: Optional[str] = None,
        artifact_kind: Optional[str] = None,
        search: Optional[str] = None,
    ) -> list[ArtifactRef]:
        await self.get(project_id)
        rows = self.store.list_artifact_refs(
            project_id,
            artifact_type=artifact_type,
            artifact_kind=artifact_kind,
            search=search,
        )
        return [ArtifactRef.from_row(r) for r in rows]

    async def export_artifacts(
        self,
        project_id: str,
        artifact_ids: Optional[list[str]] = None,
    ) -> bytes:
        await self.get(project_id)
        if artifact_ids:
            refs = []
            for aid in artifact_ids:
                ref = self.store.get_artifact_ref(aid)
                if ref and ref["project_id"] == project_id:
                    refs.append(ref)
        else:
            refs = self.store.list_artifact_refs(project_id)
        if not refs:
            raise ArtifactNotFound("no artifacts to export")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for ref in refs:
                try:
                    result = await self._call_artifacts_engine(
                        "artifact.get", {"artifact_id": ref["artifact_id"]}
                    )
                    content = result.get("artifact", {}).get("content", "")
                    filename = f"{ref['artifact_name'] or ref['artifact_id']}"
                    ext = _type_extension(ref.get("artifact_type", ""))
                    zf.writestr(f"{filename}{ext}", content)
                except Exception:
                    logger.warning("export failed for artifact=%s, skipping", ref["artifact_id"])
        logger.info("exported artifacts project=%s count=%d", project_id, len(refs))
        return buf.getvalue()

    async def remove_artifact(self, artifact_id: str) -> bool:
        existing = self.store.get_artifact_ref(artifact_id)
        if not existing:
            raise ArtifactNotFound(artifact_id)
        removed = self.store.remove_artifact_ref(artifact_id)
        if removed:
            logger.info("artifact ref removed artifact=%s", artifact_id)
        return removed
