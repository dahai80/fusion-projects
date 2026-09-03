import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

from project_service import config, metrics
from project_service.engine.gateway_client import GatewayClient, GatewayError
from project_service.engine.project_manager import ProjectManager, ProjectNotFound
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
        upstream: Optional[GatewayClient] = None,
    ) -> None:
        self.store = store or ProjectStore()
        self.project_manager = project_manager or ProjectManager()
        self.upstream = upstream or GatewayClient()
        self._kb_lock = asyncio.Lock()
        self._index_sem = asyncio.Semaphore(config.RAG_INDEX_CONCURRENCY)

    async def _ensure_project(self, project_id: str) -> None:
        row = self.store.get_project(project_id)
        if not row:
            raise ProjectNotFound(project_id)

    async def _ensure_kb(self, project_id: str) -> str:
        async with self._kb_lock:
            project = self.store.get_project(project_id)
            existing_kb_id = project.get("kb_id") if project else None
            if existing_kb_id:
                if await self._kb_exists(existing_kb_id):
                    return existing_kb_id
                logger.warning("stale kb_id=%s for project=%s, clearing and recreating", existing_kb_id, project_id)
                self.store.update_project(project_id, {"kb_id": None})
            name = project.get("name", project_id) if project else project_id
            try:
                create_result = await self.upstream.rag_create_kb(name=name, embedding_model=config.RAG_EMBEDDING_MODEL)
            except GatewayError as e:
                raise RAGError(f"failed to create rag kb: {e}") from e
            rag_kb_id = create_result.get("id")
            if not rag_kb_id:
                raise RAGError("rag kb created but no id returned")
            self.store.update_project(project_id, {"kb_id": rag_kb_id})
            logger.info("created rag kb for project=%s kb_id=%s", project_id, rag_kb_id)
            return rag_kb_id

    async def _kb_exists(self, kb_id: str) -> bool:
        status = await self.upstream.rag_kb_status(kb_id=kb_id)
        if status == 200:
            return True
        if status == 404:
            logger.warning("rag kb not found kb_id=%s, treating as stale", kb_id)
            return False
        logger.warning("rag kb probe ambiguous kb_id=%s status=%s, keep existing (transient err)", kb_id, status)
        return True

    async def get_always_include_context(self, project_id: str) -> tuple[str, list[dict]]:
        await self._ensure_project(project_id)
        files = await asyncio.to_thread(self.store.list_always_include_files, project_id)
        if not files:
            return "", []
        parts: list[str] = []
        sources: list[dict] = []
        for f in files:
            try:
                text = Path(f["file_path"]).read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                logger.warning("always_include read failed file=%s err=%s", f["id"], e)
                continue
            if not text.strip():
                continue
            parts.append(
                f"<always_included source=\"{f['name']}\">\n{text}\n</always_included>"
            )
            sources.append({
                "file_id": f["id"],
                "file_name": f["name"],
                "score": 1.0,
                "always_include": True,
                "snippet": text[:200],
            })
        if not parts:
            return "", []
        header = (
            "以下是标记为必含的专案知识文件（全量注入，不经检索召回）。"
            "与用户指令冲突时以用户指令为准。"
        )
        return header + "\n\n" + "\n\n".join(parts), sources

    async def index_file(self, file_id: str) -> dict:
        kfile = self.store.get_knowledge_file(file_id)
        if not kfile:
            raise RAGError(f"knowledge file not found: {file_id}")
        self.store.update_knowledge_file(file_id, {"index_status": "INDEXING"})
        try:
            kb_id = await self._ensure_kb(kfile["project_id"])
            result = await self.upstream.rag_upload_doc(
                kb_id=kb_id,
                file_path=kfile["file_path"],
                contextualize=True,
            )
        except GatewayError as e:
            self.store.update_knowledge_file(file_id, {"index_status": "FAILED"})
            logger.error("rag index failed file=%s error=%s", file_id, e)
            return {"error": "gateway_error", "detail": str(e)}
        except Exception as e:
            self.store.update_knowledge_file(file_id, {"index_status": "FAILED"})
            logger.error("rag index unexpected failure file=%s error=%s", file_id, e)
            raise RAGError(f"index failed: {e}") from e
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

    async def index_folder(self, folder_id: str, project_id: Optional[str] = None) -> list[dict]:
        folder = self.store.get_folder(folder_id)
        if not folder:
            raise RAGError(f"folder not found: {folder_id}")
        if project_id is not None and folder["project_id"] != project_id:
            logger.warning("index_folder ownership mismatch folder=%s folder_project=%s req_project=%s", folder_id, folder["project_id"], project_id)
            raise RAGError(f"folder {folder_id} not in project {project_id}")
        files = self.store.list_knowledge_files(folder["project_id"], folder_id=folder_id)
        pending = [f["id"] for f in files if f["index_status"] in ("PENDING", "FAILED")]

        async def _index_one(fid: str) -> dict:
            async with self._index_sem:
                try:
                    return await self.index_file(fid)
                except RAGError as e:
                    logger.warning("index_folder file=%s failed: %s", fid, e)
                    return {"error": "index_failed", "file_id": fid, "detail": str(e)}

        results = await asyncio.gather(*[_index_one(fid) for fid in pending])
        results = [r for r in results if r]
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
        project = await asyncio.to_thread(self.store.get_project, project_id)
        rag_mode = mode if mode is not None else project.get("rag_mode", "AUTO")
        rag_top_k = top_k if top_k is not None else project.get("rag_top_k", 5)
        rag_threshold = threshold if threshold is not None else project.get("rag_threshold", 0.65)
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
        storage_root = str(config.BASE_DIR)
        prefixes: list[str | None] = []
        if folder_ids:
            for fid in folder_ids:
                folder_row = await asyncio.to_thread(self.store.get_folder, fid)
                if not folder_row:
                    raise RAGError(f"folder not found: {fid}")
                if folder_row["project_id"] != project_id:
                    logger.warning("query folder ownership mismatch folder=%s folder_project=%s req_project=%s", fid, folder_row["project_id"], project_id)
                    raise RAGError(f"folder {fid} not in project {project_id}")
                prefixes.append(f"{storage_root}/storage/{project_id}/knowledge/{fid}")
        else:
            prefixes = [None]

        async def _search_one(prefix: Optional[str]) -> list:
            try:
                result = await self.upstream.rag_search(
                    kb_id=kb_id, query=query_text, top_k=rag_top_k, folder_prefix=prefix,
                )
            except GatewayError as e:
                logger.warning("rag query failed project=%s prefix=%s error=%s", project_id, prefix, e)
                return []
            return result if isinstance(result, list) else result.get("results", result.get("data", []))

        per_folder = await asyncio.gather(*[_search_one(p) for p in prefixes])
        raw_groups = [it for it in per_folder if it]
        total_raw = sum(len(g) for g in raw_groups)
        items = self._merge_results(raw_groups, top_k=rag_top_k)
        below_threshold = max(0, total_raw - len(items))
        metrics.record_rag_query(recalled=len(items), below_threshold=below_threshold)
        sources = self._to_sources(items, project_id)
        logger.info(
            "rag query project=%s mode=%s folders=%d raw=%d recalled=%d below=%d",
            project_id, rag_mode, len(prefixes), total_raw, len(items), below_threshold,
        )
        return {"results": items, "sources": sources, "mode": rag_mode}

    @staticmethod
    def _merge_results(groups: list[list], *, top_k: int) -> list:
        seen: set = set()
        merged: list = []
        for group in groups:
            for it in group:
                if not isinstance(it, dict):
                    continue
                key = it.get("doc_id") or it.get("id") or it.get("document_id") or id(it)
                if key in seen:
                    continue
                seen.add(key)
                merged.append(it)
        def _score(it: dict) -> float:
            s = it.get("score")
            if isinstance(s, (int, float)):
                return float(s)
            return 0.0
        merged.sort(key=_score, reverse=True)
        return merged[:top_k]

    def _to_sources(self, items: list, project_id: str) -> list[dict]:
        files = {f["id"]: f for f in self.store.list_knowledge_files(project_id)}
        by_doc: dict[str, str] = {f["rag_doc_id"]: f["name"] for f in files.values() if f.get("rag_doc_id")}
        sources: list[dict] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            doc_id = it.get("doc_id") or it.get("id") or it.get("document_id")
            file_name = (
                it.get("doc_name") or it.get("name")
                or by_doc.get(doc_id) or "unknown"
            )
            file_id = None
            for fid, f in files.items():
                if f.get("rag_doc_id") and f["rag_doc_id"] == doc_id:
                    file_id = fid
                    break
            text = it.get("text") or it.get("content") or ""
            sources.append({
                "file_id": file_id,
                "file_name": file_name,
                "doc_id": doc_id,
                "score": it.get("score"),
                "snippet": text[:200] if text else "",
            })
        return sources

    async def remove_file_index(self, file_id: str) -> dict:
        kfile = self.store.get_knowledge_file(file_id)
        if not kfile:
            raise RAGError(f"knowledge file not found: {file_id}")
        doc_id = kfile.get("rag_doc_id")
        if not doc_id:
            self.store.update_knowledge_file(file_id, {"index_status": "PENDING", "rag_doc_id": None})
            return {"status": "no_index"}
        kb_id = await self._ensure_kb(kfile["project_id"])
        try:
            result = await self.upstream.rag_delete_doc(kb_id=kb_id, doc_id=doc_id)
        except GatewayError as e:
            logger.error("rag doc remove failed file=%s doc=%s error=%s", file_id, doc_id, e)
            return {"error": "gateway_error", "detail": str(e)}
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
