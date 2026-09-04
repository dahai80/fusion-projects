import asyncio
import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Optional

from project_service import config
from project_service.engine.project_manager import ProjectManager, ProjectNotFound
from project_service.engine.rag_coordinator import RAGCoordinator
from project_service.models.knowledge import (
    FileIndexStatus,
    FolderCreate,
    FolderUpdate,
    KnowledgeFile,
    KnowledgeFolder,
)
from project_service.store.file_store import FileStore, QuotaExceeded
from project_service.store.project_store import ProjectStore

logger = logging.getLogger(__name__)


class KnowledgeError(Exception):
    pass


class FolderNotFound(KnowledgeError):
    pass


class KnowledgeFileNotFound(KnowledgeError):
    pass


class KnowledgeQuotaExceeded(KnowledgeError):
    pass


_SENSITIVE_DIRS = ("/etc", "/private/etc", "/System", "/usr", "/proc", "/sys")
_SENSITIVE_NAMES = (".ssh", "secret.key", ".env", ".aws", ".gnupg", "id_rsa")


def _is_under(path: Path, base: str) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def _validate_source(source_path: str, project_id: Optional[str] = None) -> Path:
    # allowlist model: a source file may only be read from the project's own
    # storage dir, an explicitly configured import root, or the system temp dir
    # (legitimate staging area for browser/CLI uploads). a denylist alone always
    # misses a sensitive path, so the allowlist is the primary boundary; the
    # sensitive-name/dir checks below are defense-in-depth on top of it.
    src = Path(source_path).resolve()
    if not src.exists():
        raise KnowledgeError(f"source file not found: {source_path}")
    if src.is_dir():
        raise KnowledgeError(f"source path is a directory: {source_path}")
    for sensitive in _SENSITIVE_DIRS:
        if _is_under(src, sensitive):
            logger.warning("rejected sensitive source path: %s", source_path)
            raise KnowledgeError("source path is in a restricted system directory")
    name_lower = src.name.lower()
    for bad in _SENSITIVE_NAMES:
        if bad in name_lower or bad in str(src).lower():
            logger.warning("rejected sensitive source name: %s", source_path)
            raise KnowledgeError("source path references a restricted file")
    allowed_roots: list[str] = list(config.KNOWLEDGE_IMPORT_ROOTS)
    allowed_roots.append(config.TEMP_IMPORT_ROOT)
    if project_id:
        allowed_roots.append(str(config.STORAGE_DIR / project_id / "knowledge"))
    allowed = any(_is_under(src, root) for root in allowed_roots if root)
    if not allowed:
        logger.warning("rejected source path outside allowlist: %s", source_path)
        raise KnowledgeError("source path is outside permitted import directories")
    size = src.stat().st_size
    if size > config.KNOWLEDGE_MAX_FILE_BYTES:
        raise KnowledgeError(
            f"source file too large: {size} bytes (limit {config.KNOWLEDGE_MAX_FILE_BYTES})"
        )
    return src


def _sanitize_name(original_name: str) -> str:
    safe = Path(original_name).name
    if not safe or safe in (".", ".."):
        logger.warning("rejected unsafe original_name: %s", original_name)
        raise KnowledgeError(f"invalid original_name: {original_name}")
    return safe


def _atomic_copy(src: Path, dest: Path) -> None:
    # copy src to a sibling temp file, fsync, then atomically rename onto dest.
    # never leaves a half-written file at dest_path; a crash mid-copy only
    # orphans the temp file (cleaned on next attempt). replaces the old
    # unlink-then-copy pattern that lost data irreversibly if copy2 failed.
    dest_dir = dest.parent
    dest_dir.mkdir(parents=True, exist_ok=True)
    tmp = dest_dir / f".{dest.name}.{uuid.uuid4().hex[:8]}.partial"
    try:
        shutil.copy2(str(src), str(tmp))
        with open(tmp, "rb") as fh:
            os.fsync(fh.fileno())
        os.replace(str(tmp), str(dest))
    except BaseException:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise


class KnowledgeManager:
    def __init__(
        self,
        store: Optional[ProjectStore] = None,
        project_manager: Optional[ProjectManager] = None,
        file_store: Optional[FileStore] = None,
        rag_coordinator: Optional[RAGCoordinator] = None,
    ) -> None:
        self.store = store or ProjectStore()
        self.project_manager = project_manager or ProjectManager()
        self.file_store = file_store or FileStore()
        self.rag_coordinator = rag_coordinator

    async def _ensure_project(self, project_id: str) -> None:
        row = await asyncio.to_thread(self.store.get_project, project_id)
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
        row = await asyncio.to_thread(self.store.create_folder, data)
        logger.info("folder created id=%s project=%s name=%s", row["id"], project_id, data["name"])
        return KnowledgeFolder.from_row(row)

    async def get_folder(self, folder_id: str) -> KnowledgeFolder:
        row = await asyncio.to_thread(self.store.get_folder, folder_id)
        if not row:
            raise FolderNotFound(folder_id)
        return KnowledgeFolder.from_row(row)

    async def list_folders(
        self,
        project_id: str,
        parent_id: Optional[str] = None,
    ) -> list[KnowledgeFolder]:
        await self._ensure_project(project_id)
        rows = await asyncio.to_thread(self.store.list_folders, project_id, parent_id)
        return [KnowledgeFolder.from_row(r) for r in rows]

    async def update_folder(
        self,
        folder_id: str,
        payload: FolderUpdate,
    ) -> KnowledgeFolder:
        fields = payload.model_dump(exclude_unset=True)
        row = await asyncio.to_thread(self.store.update_folder, folder_id, fields)
        if not row:
            raise FolderNotFound(folder_id)
        logger.info("folder updated id=%s fields=%s", folder_id, list(fields.keys()))
        return KnowledgeFolder.from_row(row)

    async def delete_folder(self, folder_id: str) -> None:
        folder = await asyncio.to_thread(self.store.get_folder, folder_id)
        if not folder:
            raise FolderNotFound(folder_id)
        files = await asyncio.to_thread(self.store.list_knowledge_files, folder["project_id"], folder_id)
        for f in files:
            await self.delete_file(f["id"])
        if not await asyncio.to_thread(self.store.delete_folder, folder_id):
            raise FolderNotFound(folder_id)
        logger.info("folder deleted id=%s files_cleaned=%d", folder_id, len(files))

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
        row = await asyncio.to_thread(self.store.create_knowledge_file, data)
        logger.info("knowledge_file created id=%s project=%s name=%s", row["id"], project_id, name)
        return KnowledgeFile.from_row(row)

    async def get_file(self, file_id: str) -> KnowledgeFile:
        row = await asyncio.to_thread(self.store.get_knowledge_file, file_id)
        if not row:
            raise KnowledgeFileNotFound(file_id)
        return KnowledgeFile.from_row(row)

    async def list_files(
        self,
        project_id: str,
        folder_id: Optional[str] = None,
    ) -> list[KnowledgeFile]:
        await self._ensure_project(project_id)
        rows = await asyncio.to_thread(self.store.list_knowledge_files, project_id, folder_id)
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
        row = await asyncio.to_thread(self.store.update_knowledge_file, file_id, fields)
        if not row:
            raise KnowledgeFileNotFound(file_id)
        logger.info("knowledge_file status updated id=%s status=%s", file_id, index_status)
        return KnowledgeFile.from_row(row)

    async def delete_file(self, file_id: str) -> None:
        kfile = await asyncio.to_thread(self.store.get_knowledge_file, file_id)
        if not kfile:
            raise KnowledgeFileNotFound(file_id)
        if self.rag_coordinator is not None:
            try:
                await self.rag_coordinator.remove_file_index(file_id)
            except Exception as e:
                logger.warning("rag index cleanup failed file=%s err=%s (continuing delete)", file_id, e)
        old_path = Path(kfile["file_path"])
        try:
            if old_path.exists():
                old_path.unlink()
        except OSError as e:
            logger.warning("disk unlink failed file=%s path=%s err=%s", file_id, old_path, e)
        if not await asyncio.to_thread(self.store.delete_knowledge_file, file_id):
            raise KnowledgeFileNotFound(file_id)
        logger.info("knowledge_file deleted id=%s", file_id)

    async def list_file_statuses(self, project_id: str) -> list[FileIndexStatus]:
        await self._ensure_project(project_id)
        rows = await asyncio.to_thread(self.store.list_knowledge_files, project_id)
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
        src = _validate_source(source_path, project_id=project_id)
        dest_dir = self.file_store.project_dir(project_id) / "knowledge"
        if folder_id:
            folder = await asyncio.to_thread(self.store.get_folder, folder_id)
            if folder and folder["project_id"] == project_id:
                dest_dir = dest_dir / folder_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        file_size = src.stat().st_size
        try:
            self.file_store.check_quota(project_id, file_size)
        except QuotaExceeded as e:
            logger.warning("upload rejected by quota project=%s file=%s size=%s err=%s", project_id, original_name, file_size, e)
            raise KnowledgeQuotaExceeded(str(e)) from e
        safe_name = _sanitize_name(original_name)
        dest_path = (dest_dir / safe_name).resolve()
        if not dest_path.is_relative_to(dest_dir.resolve()):
            raise KnowledgeError("path traversal in original_name")
        if dest_path.exists():
            stem = dest_path.stem
            suffix = dest_path.suffix
            dest_path = dest_dir / f"{stem}_{uuid.uuid4().hex[:8]}{suffix}"
        _atomic_copy(src, dest_path)
        name = dest_path.stem
        kfile = await self.create_file(
            project_id,
            folder_id=folder_id,
            name=name,
            original_name=safe_name,
            file_path=str(dest_path),
            file_size=file_size,
            mime_type=mime_type,
        )
        logger.info("file uploaded id=%s path=%s project=%s", kfile.id, dest_path, project_id)
        if self.rag_coordinator is not None:
            try:
                await self.rag_coordinator.index_file(kfile.id)
                logger.info("auto-index triggered for file=%s project=%s", kfile.id, project_id)
            except Exception as e:
                logger.warning("auto-index failed file=%s project=%s err=%s (left PENDING)", kfile.id, project_id, e)
        # re-read so the returned object reflects the post-index row (INDEXED/rag_doc_id),
        # not the stale pre-index row from create_file.
        refreshed = await asyncio.to_thread(self.store.get_knowledge_file, kfile.id)
        if refreshed:
            return KnowledgeFile.from_row(refreshed)
        return kfile

    async def replace_file(
        self,
        file_id: str,
        source_path: str,
    ) -> KnowledgeFile:
        existing = await asyncio.to_thread(self.store.get_knowledge_file, file_id)
        if not existing:
            raise KnowledgeFileNotFound(file_id)
        src = _validate_source(source_path, project_id=existing["project_id"])
        if self.rag_coordinator is not None:
            try:
                await self.rag_coordinator.remove_file_index(file_id)
            except Exception as e:
                logger.warning("old rag index removal failed file=%s err=%s (continuing replace)", file_id, e)
        old_path = Path(existing["file_path"])
        file_size = src.stat().st_size
        try:
            self.file_store.check_quota(existing["project_id"], file_size)
        except QuotaExceeded as e:
            logger.warning("replace rejected by quota file=%s size=%s err=%s", file_id, file_size, e)
            raise KnowledgeQuotaExceeded(str(e)) from e
        # atomic copy-onto-old: os.replace atomically swaps the inode, so the
        # old file content stays intact until the new content is fully on disk.
        # a crash mid-copy orphans the temp file; old_path is never left empty.
        _atomic_copy(src, old_path)
        await asyncio.to_thread(self.store.update_knowledge_file, file_id, {
            "file_size": file_size,
            "index_status": "PENDING",
            "rag_doc_id": None,
        })
        if self.rag_coordinator is not None:
            try:
                await self.rag_coordinator.index_file(file_id)
                logger.info("re-index triggered for replaced file=%s", file_id)
            except Exception as e:
                logger.warning("re-index failed file=%s err=%s (left PENDING)", file_id, e)
        row = await asyncio.to_thread(self.store.get_knowledge_file, file_id)
        logger.info("file replaced id=%s new_size=%d", file_id, file_size)
        return KnowledgeFile.from_row(row)

    async def rename_file(self, file_id: str, name: str) -> KnowledgeFile:
        row = await asyncio.to_thread(self.store.update_knowledge_file, file_id, {"name": name})
        if not row:
            raise KnowledgeFileNotFound(file_id)
        logger.info("file renamed id=%s name=%s", file_id, name)
        return KnowledgeFile.from_row(row)

    async def move_file(self, file_id: str, folder_id: Optional[str]) -> KnowledgeFile:
        row = await asyncio.to_thread(self.store.update_knowledge_file, file_id, {"folder_id": folder_id})
        if not row:
            raise KnowledgeFileNotFound(file_id)
        logger.info("file moved id=%s folder=%s", file_id, folder_id)
        return KnowledgeFile.from_row(row)

    async def set_always_include(self, file_id: str, always_include: bool) -> KnowledgeFile:
        row = await asyncio.to_thread(self.store.update_knowledge_file, file_id, {"always_include": 1 if always_include else 0})
        if not row:
            raise KnowledgeFileNotFound(file_id)
        logger.info("file always_include set id=%s value=%s", file_id, always_include)
        return KnowledgeFile.from_row(row)
