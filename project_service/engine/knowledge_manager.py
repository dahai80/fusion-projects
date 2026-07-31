import logging
import shutil
from pathlib import Path
from typing import Optional

from project_service import config
from project_service.engine.project_manager import ProjectManager, ProjectNotFound
from project_service.models.knowledge import (
    FileIndexStatus,
    FolderCreate,
    FolderUpdate,
    KnowledgeFile,
    KnowledgeFolder,
)
from project_service.store.file_store import FileStore
from project_service.store.project_store import ProjectStore

logger = logging.getLogger(__name__)


class KnowledgeError(Exception):
    pass


class FolderNotFound(KnowledgeError):
    pass


class KnowledgeFileNotFound(KnowledgeError):
    pass


class KnowledgeManager:
    def __init__(
        self,
        store: Optional[ProjectStore] = None,
        project_manager: Optional[ProjectManager] = None,
        file_store: Optional[FileStore] = None,
    ) -> None:
        self.store = store or ProjectStore()
        self.project_manager = project_manager or ProjectManager()
        self.file_store = file_store or FileStore()

    async def _ensure_project(self, project_id: str) -> None:
        row = self.store.get_project(project_id)
        if not row:
            raise ProjectNotFound(project_id)

    async def create_folder(
        self,
        project_id: str,
        payload: FolderCreate,
    ) -> KnowledgeFolder:
        await self._ensure_project(project_id)
        data = payload.model_dump()
        data["project_id"] = project_id
        row = self.store.create_folder(data)
        logger.info("folder created id=%s project=%s name=%s", row["id"], project_id, data["name"])
        return KnowledgeFolder.from_row(row)

    async def get_folder(self, folder_id: str) -> KnowledgeFolder:
        row = self.store.get_folder(folder_id)
        if not row:
            raise FolderNotFound(folder_id)
        return KnowledgeFolder.from_row(row)

    async def list_folders(
        self,
        project_id: str,
        parent_id: Optional[str] = None,
    ) -> list[KnowledgeFolder]:
        await self._ensure_project(project_id)
        rows = self.store.list_folders(project_id, parent_id=parent_id)
        return [KnowledgeFolder.from_row(r) for r in rows]

    async def update_folder(
        self,
        folder_id: str,
        payload: FolderUpdate,
    ) -> KnowledgeFolder:
        fields = payload.model_dump(exclude_unset=True)
        row = self.store.update_folder(folder_id, fields)
        if not row:
            raise FolderNotFound(folder_id)
        logger.info("folder updated id=%s fields=%s", folder_id, list(fields.keys()))
        return KnowledgeFolder.from_row(row)

    async def delete_folder(self, folder_id: str) -> None:
        if not self.store.delete_folder(folder_id):
            raise FolderNotFound(folder_id)
        logger.info("folder deleted id=%s", folder_id)

    async def create_file(
        self,
        project_id: str,
        *,
        folder_id: Optional[str] = None,
        name: str,
        original_name: str,
        file_path: str,
        file_size: int = 0,
        mime_type: Optional[str] = None,
    ) -> KnowledgeFile:
        await self._ensure_project(project_id)
        data = {
            "project_id": project_id,
            "folder_id": folder_id,
            "name": name,
            "original_name": original_name,
            "file_path": file_path,
            "file_size": file_size,
            "mime_type": mime_type,
        }
        row = self.store.create_knowledge_file(data)
        logger.info("knowledge_file created id=%s project=%s name=%s", row["id"], project_id, name)
        return KnowledgeFile.from_row(row)

    async def get_file(self, file_id: str) -> KnowledgeFile:
        row = self.store.get_knowledge_file(file_id)
        if not row:
            raise KnowledgeFileNotFound(file_id)
        return KnowledgeFile.from_row(row)

    async def list_files(
        self,
        project_id: str,
        folder_id: Optional[str] = None,
    ) -> list[KnowledgeFile]:
        await self._ensure_project(project_id)
        rows = self.store.list_knowledge_files(project_id, folder_id=folder_id)
        return [KnowledgeFile.from_row(r) for r in rows]

    async def update_file_status(
        self,
        file_id: str,
        index_status: str,
        rag_doc_id: Optional[str] = None,
    ) -> KnowledgeFile:
        fields: dict = {"index_status": index_status}
        if rag_doc_id is not None:
            fields["rag_doc_id"] = rag_doc_id
        row = self.store.update_knowledge_file(file_id, fields)
        if not row:
            raise KnowledgeFileNotFound(file_id)
        logger.info("knowledge_file status updated id=%s status=%s", file_id, index_status)
        return KnowledgeFile.from_row(row)

    async def delete_file(self, file_id: str) -> None:
        if not self.store.delete_knowledge_file(file_id):
            raise KnowledgeFileNotFound(file_id)
        logger.info("knowledge_file deleted id=%s", file_id)

    async def list_file_statuses(self, project_id: str) -> list[FileIndexStatus]:
        await self._ensure_project(project_id)
        rows = self.store.list_knowledge_files(project_id)
        return [
            FileIndexStatus(file_id=r["id"], name=r["name"], index_status=r["index_status"])
            for r in rows
        ]

    async def upload_file(
        self,
        project_id: str,
        source_path: str,
        original_name: str,
        folder_id: Optional[str] = None,
        mime_type: Optional[str] = None,
    ) -> KnowledgeFile:
        await self._ensure_project(project_id)
        src = Path(source_path)
        if not src.exists():
            raise KnowledgeError(f"source file not found: {source_path}")
        dest_dir = self.file_store.project_dir(project_id) / "knowledge"
        if folder_id:
            folder = self.store.get_folder(folder_id)
            if folder and folder["project_id"] == project_id:
                dest_dir = dest_dir / folder_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        file_size = src.stat().st_size
        dest_path = dest_dir / original_name
        if dest_path.exists():
            stem = dest_path.stem
            suffix = dest_path.suffix
            import uuid
            dest_path = dest_dir / f"{stem}_{uuid.uuid4().hex[:8]}{suffix}"
        shutil.copy2(str(src), str(dest_path))
        name = dest_path.stem
        kfile = await self.create_file(
            project_id,
            folder_id=folder_id,
            name=name,
            original_name=original_name,
            file_path=str(dest_path),
            file_size=file_size,
            mime_type=mime_type,
        )
        logger.info("file uploaded id=%s path=%s project=%s", kfile.id, dest_path, project_id)
        return kfile

    async def replace_file(
        self,
        file_id: str,
        source_path: str,
    ) -> KnowledgeFile:
        existing = self.store.get_knowledge_file(file_id)
        if not existing:
            raise KnowledgeFileNotFound(file_id)
        src = Path(source_path)
        if not src.exists():
            raise KnowledgeError(f"source file not found: {source_path}")
        old_path = Path(existing["file_path"])
        if old_path.exists():
            old_path.unlink()
        shutil.copy2(str(src), str(old_path))
        file_size = src.stat().st_size
        row = self.store.update_knowledge_file(file_id, {
            "file_size": file_size,
            "index_status": "PENDING",
        })
        logger.info("file replaced id=%s new_size=%d", file_id, file_size)
        return KnowledgeFile.from_row(row)

    async def rename_file(self, file_id: str, name: str) -> KnowledgeFile:
        row = self.store.update_knowledge_file(file_id, {"name": name})
        if not row:
            raise KnowledgeFileNotFound(file_id)
        logger.info("file renamed id=%s name=%s", file_id, name)
        return KnowledgeFile.from_row(row)

    async def move_file(self, file_id: str, folder_id: Optional[str]) -> KnowledgeFile:
        row = self.store.update_knowledge_file(file_id, {"folder_id": folder_id})
        if not row:
            raise KnowledgeFileNotFound(file_id)
        logger.info("file moved id=%s folder=%s", file_id, folder_id)
        return KnowledgeFile.from_row(row)
