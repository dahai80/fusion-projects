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
        # per-project create-locks: serialize kb creation for ONE project (so 8
        # concurrent index_file calls don't race to create 8 kbs) while letting
        # different projects create concurrently. replaces the single global
        # _kb_lock that serialized all projects behind one HTTP call (M6).
        self._kb_create_locks: dict[str, asyncio.Lock] = {}
        self._index_sem = asyncio.Semaphore(config.RAG_INDEX_CONCURRENCY)

    async def _ensure_project(self, project_id: str) -> None:
        row = await asyncio.to_thread(self.store.get_project, project_id)
        if not row:
            raise ProjectNotFound(project_id)

    def _kb_create_lock(self, project_id: str) -> asyncio.Lock:
        lock = self._kb_create_locks.get(project_id)
        if lock is None:
            lock = asyncio.Lock()
            self._kb_create_locks[project_id] = lock
        return lock

    async def _ensure_kb(self, project_id: str) -> str:
        # fast path: read kb_id off-loop, no lock. only the create path is
        # serialized per-project, so a hot project with a live kb never blocks.
        project = await asyncio.to_thread(self.store.get_project, project_id)
        existing_kb_id = project.get("kb_id") if project else None
        if existing_kb_id:
            if await self._kb_exists(existing_kb_id):
                return existing_kb_id
            logger.warning("stale kb_id=%s for project=%s, clearing and recreating", existing_kb_id, project_id)
            await asyncio.to_thread(self.store.update_project, project_id, {"kb_id": None})
        # serialize creation for THIS project only: concurrent callers that saw
        # no kb_id wait here, then re-read after the first creator persists it.
        async with self._kb_create_lock(project_id):
            project = await asyncio.to_thread(self.store.get_project, project_id)
            existing_kb_id = project.get("kb_id") if project else None
            if existing_kb_id:
                if await self._kb_exists(existing_kb_id):
                    return existing_kb_id
            name = project.get("name", project_id) if project else project_id
            try:
                create_result = await self.upstream.rag_create_kb(name=name, embedding_model=config.RAG_EMBEDDING_MODEL)
            except GatewayError as e:
                raise RAGError(f"failed to create rag kb: {e}") from e
            rag_kb_id = create_result.get("id")
            if not rag_kb_id:
                raise RAGError("rag kb created but no id returned")
            await asyncio.to_thread(self.store.update_project, project_id, {"kb_id": rag_kb_id})
            logger.info("created rag kb for project=%s kb_id=%s", project_id, rag_kb_id)
            return rag_kb_id

    async def _kb_exists(self, kb_id: str) -> bool:
        status = await self.upstream.rag_kb_status(kb_id=kb_id)
        if status == 200:
            return True
        if status == 404:
            logger.warning("rag kb not found kb_id=%s, treating as stale", kb_id)
            return False
        # ambiguous (5xx/-1/timeout): previously returned True and kept a
        # possibly-dead kb_id, cascading all later ops to failure. retry once;
        # still ambiguous -> treat as NOT existing so _ensure_kb recreates.
        logger.warning("rag kb probe ambiguous kb_id=%s status=%s, retrying", kb_id, status)
        status2 = await self.upstream.rag_kb_status(kb_id=kb_id)
        if status2 == 200:
            return True
        if status2 == 404:
            return False
        logger.warning("rag kb probe still ambiguous kb_id=%s status=%s, recreating", kb_id, status2)
        return False

    async def get_always_include_context(self, project_id: str) -> tuple[str, list[dict]]:
        await self._ensure_project(project_id)
        files = await asyncio.to_thread(self.store.list_always_include_files, project_id)
        if not files:
            return "", []
        # containment root: always_include files must live under this project's
        # knowledge dir; reject any file_path that escapes (arbitrary file read).
        knowledge_root = (config.STORAGE_DIR / project_id / "knowledge").resolve()
        parts: list[str] = []
        sources: list[dict] = []
        for f in files:
            try:
                fpath = Path(f["file_path"]).resolve()
                if not fpath.is_relative_to(knowledge_root):
                    logger.warning("always_include path escapes project root file=%s path=%s", f["id"], fpath)
                    continue
                if fpath.stat().st_size > config.ALWAYS_INCLUDE_MAX_BYTES:
                    logger.warning("always_include file too large file=%s size=%s", f["id"], fpath.stat().st_size)
                    continue
                text = fpath.read_text(encoding="utf-8", errors="replace")
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
        kfile = await asyncio.to_thread(self.store.get_knowledge_file, file_id)
        if not kfile:
            raise RAGError(f"knowledge file not found: {file_id}")
        await asyncio.to_thread(self.store.update_knowledge_file, file_id, {"index_status": "INDEXING"})
        try:
            kb_id = await self._ensure_kb(kfile["project_id"])
            result = await self.upstream.rag_upload_doc(
                kb_id=kb_id,
                file_path=kfile["file_path"],
                contextualize=True,
            )
        except GatewayError as e:
            await asyncio.to_thread(self.store.update_knowledge_file, file_id, {"index_status": "FAILED"})
            logger.error("rag index failed file=%s error=%s", file_id, e)
            raise RAGError(f"index failed: {e}") from e
        except Exception as e:
            await asyncio.to_thread(self.store.update_knowledge_file, file_id, {"index_status": "FAILED"})
            logger.error("rag index unexpected failure file=%s error=%s", file_id, e)
            raise RAGError(f"index failed: {e}") from e
        doc_id = result.get("doc_id") or result.get("document_id")
        if doc_id:
            await asyncio.to_thread(self.store.update_knowledge_file, file_id, {
                "index_status": "INDEXED",
                "rag_doc_id": doc_id,
            })
        else:
            await asyncio.to_thread(self.store.update_knowledge_file, file_id, {"index_status": "INDEXED"})
        logger.info("rag index complete file=%s doc=%s", file_id, doc_id)
        return result

    async def index_folder(self, folder_id: str, project_id: Optional[str] = None) -> list[dict]:
        folder = await asyncio.to_thread(self.store.get_folder, folder_id)
        if not folder:
            raise RAGError(f"folder not found: {folder_id}")
        if project_id is not None and folder["project_id"] != project_id:
            logger.warning("index_folder ownership mismatch folder=%s folder_project=%s req_project=%s", folder_id, folder["project_id"], project_id)
            raise RAGError(f"folder {folder_id} not in project {project_id}")
        files = await asyncio.to_thread(self.store.list_knowledge_files, folder["project_id"], folder_id)
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
        # clamp inputs: negative/oversized top_k and out-of-range threshold
        # would distort ranking or force giant upstream responses.
        try:
            rag_top_k = max(1, min(int(rag_top_k), config.RAG_MAX_TOP_K))
        except (TypeError, ValueError):
            rag_top_k = config.DEFAULT_RAG_TOP_K
        try:
            rag_threshold = max(0.0, min(float(rag_threshold), 1.0))
        except (TypeError, ValueError):
            rag_threshold = config.DEFAULT_RAG_THRESHOLD
        if rag_mode == "MANUAL" and not folder_ids:
            logger.warning("MANUAL RAG mode but no folder_ids specified, returning empty")
            return {"results": [], "mode": rag_mode}
        scope_folder_ids = None
        if rag_mode == "MANUAL" and folder_ids:
            scope_folder_ids = json.dumps(folder_ids)
        await asyncio.to_thread(self.store.create_rag_query, {
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
            if len(folder_ids) > config.RAG_MAX_FOLDER_SCOPE:
                logger.warning(
                    "folder scope too large %d > %d, truncating project=%s",
                    len(folder_ids), config.RAG_MAX_FOLDER_SCOPE, project_id,
                )
                folder_ids = folder_ids[: config.RAG_MAX_FOLDER_SCOPE]
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

        upstream_failures: list[str] = []

        async def _search_one(prefix: Optional[str]) -> list:
            try:
                result = await self.upstream.rag_search(
                    kb_id=kb_id, query=query_text, top_k=rag_top_k, folder_prefix=prefix,
                )
            except GatewayError as e:
                logger.warning("rag query failed project=%s prefix=%s error=%s", project_id, prefix, e)
                upstream_failures.append(str(e))
                return []
            return result if isinstance(result, list) else result.get("results", result.get("data", []))

        per_folder = await asyncio.gather(*[_search_one(p) for p in prefixes])
        raw_groups = [it for it in per_folder if it]
        total_raw = sum(len(g) for g in raw_groups)
        items, dropped_below = self._merge_results(raw_groups, top_k=rag_top_k, threshold=rag_threshold)
        metrics.record_rag_query(recalled=len(items), below_threshold=dropped_below)
        sources = await self._to_sources(items, project_id)
        logger.info(
            "rag query project=%s mode=%s folders=%d raw=%d recalled=%d below=%d",
            project_id, rag_mode, len(prefixes), total_raw, len(items), dropped_below,
        )
        result: dict = {"results": items, "sources": sources, "mode": rag_mode}
        # surface upstream failure so direct callers (project.rag.query RPC) can
        # distinguish "no docs" from "kb backend dead". the chat path checks
        # `error not in result` and degrades to no-RAG, so chat stays best-effort.
        if upstream_failures and len(upstream_failures) == len(prefixes):
            result["error"] = "rag upstream unavailable: " + upstream_failures[0]
        return result

    @staticmethod
    def _merge_results(groups: list[list], *, top_k: int, threshold: float) -> tuple[list, int]:
        seen: set = set()
        merged: list = []
        for group in groups:
            for it in group:
                if not isinstance(it, dict):
                    continue
                key = it.get("doc_id") or it.get("id") or it.get("document_id")
                if not key:
                    # no stable identity — skip rather than fall back to id(it),
                    # which is non-deterministic (object address can be reused).
                    logger.warning("rag result item lacks identity, skipping: %s", list(it.keys()))
                    continue
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
        # apply the score threshold that was previously dead code: filter out
        # items below threshold before top_k truncation, and count them.
        kept = [it for it in merged if _score(it) >= threshold]
        dropped_below = len(merged) - len(kept)
        return kept[:top_k], dropped_below

    async def _to_sources(self, items: list, project_id: str) -> list[dict]:
        files = {f["id"]: f for f in await asyncio.to_thread(self.store.list_knowledge_files, project_id)}
        # O(1) lookup by rag_doc_id instead of inner scan per item.
        by_doc_id: dict[str, str] = {}
        by_doc_name: dict[str, str] = {}
        for fid, f in files.items():
            did = f.get("rag_doc_id")
            if did:
                by_doc_id[did] = fid
                by_doc_name[did] = f["name"]
        sources: list[dict] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            doc_id = it.get("doc_id") or it.get("id") or it.get("document_id")
            file_name = (
                it.get("doc_name") or it.get("name")
                or by_doc_name.get(doc_id) or "unknown"
            )
            file_id = by_doc_id.get(doc_id)
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
        kfile = await asyncio.to_thread(self.store.get_knowledge_file, file_id)
        if not kfile:
            raise RAGError(f"knowledge file not found: {file_id}")
        doc_id = kfile.get("rag_doc_id")
        if not doc_id:
            await asyncio.to_thread(self.store.update_knowledge_file, file_id, {"index_status": "PENDING", "rag_doc_id": None})
            return {"status": "no_index"}
        kb_id = await self._ensure_kb(kfile["project_id"])
        try:
            result = await self.upstream.rag_delete_doc(kb_id=kb_id, doc_id=doc_id)
        except GatewayError as e:
            # do NOT clear rag_doc_id on failure: the upstream doc still exists,
            # clearing would orphan it and make the next remove silently no-op.
            # raise so callers (delete_file/replace_file) see the failure.
            await asyncio.to_thread(self.store.update_knowledge_file, file_id, {"index_status": "FAILED"})
            logger.error("rag doc remove failed file=%s doc=%s error=%s", file_id, doc_id, e)
            raise RAGError(f"remove failed: {e}") from e
        await asyncio.to_thread(self.store.update_knowledge_file, file_id, {"index_status": "PENDING", "rag_doc_id": None})
        logger.info("rag doc removed file=%s doc=%s", file_id, doc_id)
        return result

    async def get_rag_status(self, project_id: str) -> dict:
        await self._ensure_project(project_id)
        files = await asyncio.to_thread(self.store.list_knowledge_files, project_id)
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
