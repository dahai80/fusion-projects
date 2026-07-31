import logging
from typing import Optional

from project_service.models.project import (
    Project,
    ProjectCreate,
    ProjectListItem,
    ProjectUpdate,
)
from project_service.store.file_store import FileStore
from project_service.store.project_store import ProjectStore

logger = logging.getLogger(__name__)


class ProjectError(Exception):
    pass


class ProjectNotFound(ProjectError):
    pass


class ProjectNotArchived(ProjectError):
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
        self.store.delete_project(project_id)
        self.file_store.remove_project(project_id)
        logger.info("project deleted id=%s", project_id)
