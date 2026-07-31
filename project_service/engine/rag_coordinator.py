import json
import logging
from typing import Optional

from project_service import config
from project_service.engine.project_manager import ProjectManager, ProjectNotFound
from project_service.engine.upstream_client import UpstreamClient
from project_service.models.knowledge import KnowledgeFile
from project_service.store.project_store import ProjectStore

logger = logging.getLogger(__name__)


class RAGError(Exception):
    pass


class RAGServiceUnavailable(RAGError):
    pass


class RAGCoordinator:
    def __init__(
        self,
        store: Optional[ProjectStore] = None,
        project_manager: Optional[ProjectManager] = None,
        upstream: Optional[UpstreamClient] = None,
    ) -> None:
        self.store = store or ProjectStore()
        self.project_manager = project_manager or ProjectManager()
        self.upstream = upstream or UpstreamClient()

    async def _ensure_project(self, project_id: str) -> None:
        row = self.store.get_project(project_id)
        if not row:
            raise ProjectNotFound(project_id)

    async def _ensure_kb(self, project_id: str) -> str:
        project = self.store.get_project(project_id)
        existing_kb_id = project.get("kb_id") if project else None
        if existing_kb_id:
            return existing_kb_id
        name = project.get("name", project_id) if project else project_id
        create_result = await self.upstream.rag_create_kb(name=name, embedding_model=config.RAG_EMBEDDING_MODEL)
        if create_result.get("error"):
            raise RAGError(f"failed to create rag kb: {create_result.get('detail')}")
        rag_kb_id = create_result.get("id")
        if not rag_kb_id:
            raise RAGError("rag kb created but no id returned")
        self.store.update_project(project_id, {"kb_id": rag_kb_id})
        logger.info("created rag kb for project=%s kb_id=%s", project_id, rag_kb_id)
        return rag_kb_id

    async def index_file(self, file_id: str) -> dict:
        kfile = self.store.get_knowledge_file(file_id)
        if not kfile:
            raise RAGError(f"knowledge file not found: {file_id}")
        self.store.update_knowledge_file(file_id, {"index_status": "INDEXING"})
        kb_id = await self._ensure_kb(kfile["project_id"])
        result = await self.upstream.rag_upload_doc(
            kb_id=kb_id,
            file_path=kfile["file_path"],
            contextualize=False,
        )
        if "error" in result:
            self.store.update_knowledge_file(file_id, {"index_status": "FAILED"})
            logger.error("rag index failed file=%s error=%s", file_id, result.get("detail"))
            return result
        doc_id = result.get("doc_id") or result.get("document_id")
        if doc_id:
            self.store.update_knowledge_file(file_id, {
                "index_status": "INDEXED",
                "rag_doc_id": doc_id,
            })
        else:
            self.store.update_knowledge_file(file_id, {"index_status": "INDEXED"})
        logger.info("rag index complete file=%s doc=%s", file_id, doc_id)
        return result

    async def index_folder(self, folder_id: str) -> list[dict]:
        folder = self.store.get_folder(folder_id)
        if not folder:
            raise RAGError(f"folder not found: {folder_id}")
        files = self.store.list_knowledge_files(folder["project_id"], folder_id=folder_id)
        results = []
        for f in files:
            if f["index_status"] in ("PENDING", "FAILED"):
                result = await self.index_file(f["id"])
                results.append(result)
        logger.info("rag index folder=%s files_indexed=%d", folder_id, len(results))
        return results

    async def query(
        self,
        project_id: str,
        query_text: str,
        *,
        mode: Optional[str] = None,
        folder_ids: Optional[list[str]] = None,
        top_k: Optional[int] = None,
        threshold: Optional[float] = None,
        chat_id: Optional[str] = None,
    ) -> dict:
        await self._ensure_project(project_id)
        project = self.store.get_project(project_id)
        rag_mode = mode or project.get("rag_mode", "AUTO")
        rag_top_k = top_k or project.get("rag_top_k", 5)
        rag_threshold = threshold or project.get("rag_threshold", 0.65)
        if rag_mode == "MANUAL" and not folder_ids:
            logger.warning("MANUAL RAG mode but no folder_ids specified, returning empty")
            return {"results": [], "mode": rag_mode}
        scope_folder_ids = None
        if rag_mode == "MANUAL" and folder_ids:
            scope_folder_ids = json.dumps(folder_ids)
        self.store.create_rag_query({
            "project_id": project_id,
            "chat_id": chat_id,
            "query": query_text,
            "mode": rag_mode,
            "scope_folder_ids": scope_folder_ids,
            "top_k": rag_top_k,
            "threshold": rag_threshold,
        })
        kb_id = await self._ensure_kb(project_id)
        result = await self.upstream.rag_search(
            kb_id=kb_id,
            query=query_text,
            top_k=rag_top_k,
        )
        if "error" in result:
            logger.warning("rag query failed project=%s error=%s", project_id, result.get("detail"))
            return result
        items = result if isinstance(result, list) else result.get("results", result.get("data", []))
        logger.info("rag query project=%s mode=%s results=%d", project_id, rag_mode, len(items))
        return {"results": items, "mode": rag_mode}

    async def remove_file_index(self, file_id: str) -> dict:
        kfile = self.store.get_knowledge_file(file_id)
        if not kfile:
            raise RAGError(f"knowledge file not found: {file_id}")
        doc_id = kfile.get("rag_doc_id")
        if not doc_id:
            self.store.update_knowledge_file(file_id, {"index_status": "PENDING", "rag_doc_id": None})
            return {"status": "no_index"}
        kb_id = await self._ensure_kb(kfile["project_id"])
        result = await self.upstream.rag_delete_doc(kb_id=kb_id, doc_id=doc_id)
        self.store.update_knowledge_file(file_id, {"index_status": "PENDING", "rag_doc_id": None})
        logger.info("rag doc removed file=%s doc=%s", file_id, doc_id)
        return result

    async def get_rag_status(self, project_id: str) -> dict:
        await self._ensure_project(project_id)
        files = self.store.list_knowledge_files(project_id)
        status_counts: dict[str, int] = {}
        for f in files:
            s = f["index_status"]
            status_counts[s] = status_counts.get(s, 0) + 1
        is_healthy = await self.upstream.rag_is_healthy()
        return {
            "healthy": is_healthy,
            "file_counts": status_counts,
            "total_files": len(files),
        }
