import asyncio
import json
import logging
import os
from typing import Any, Awaitable, Callable, Optional

from pydantic import ValidationError

from project_service import config
from project_service.engine.agent_binder import AgentBinder, AgentBinderError
from project_service.engine.chat_manager import ChatManager, ChatNotFound
from project_service.engine.cowork_bridge import CoworkBridge, CoworkTaskNotFound
from project_service.engine.instruction_engine import InstructionEngine, SnapshotNotFound
from project_service.engine.knowledge_manager import (
    FolderNotFound,
    KnowledgeFileNotFound,
    KnowledgeManager,
)
from project_service.engine.project_manager import (
    ArtifactAlreadyMigrated,
    ArtifactNotFound,
    ProjectError,
    ProjectNotArchived,
    ProjectNotFound,
    ProjectManager,
)
from project_service.engine.rag_coordinator import RAGCoordinator, RAGError
from project_service.engine.upstream_client import UpstreamClient
from project_service.models.agent_binding import PromptMergeMode
from project_service.models.chat import ChatCreate, ChatMoveRequest, ChatUpdate, MessageCreate
from project_service.models.instruction import InstructionSave
from project_service.models.knowledge import FolderCreate, FolderUpdate
from project_service.models.project import ProjectCreate, ProjectUpdate
from project_service.models.audit import AuditLogEntry
from project_service.models.cowork import CoworkTrigger
from project_service.store.project_store import ProjectStore

logger = logging.getLogger(__name__)


class ProjectRPCServer:
    NAMESPACE = "project"

    def __init__(
        self,
        project_manager: Optional[ProjectManager] = None,
        instruction_engine: Optional[InstructionEngine] = None,
        chat_manager: Optional[ChatManager] = None,
        knowledge_manager: Optional[KnowledgeManager] = None,
        agent_binder: Optional[AgentBinder] = None,
        rag_coordinator: Optional[RAGCoordinator] = None,
        upstream: Optional[UpstreamClient] = None,
    ) -> None:
        store = ProjectStore()
        upstream = upstream or UpstreamClient()
        self.project_manager = project_manager or ProjectManager(store=store)
        pm_store = getattr(self.project_manager, "store", store)
        self.instruction_engine = instruction_engine or InstructionEngine(
            store=pm_store, project_manager=self.project_manager
        )
        self.chat_manager = chat_manager or ChatManager(
            store=pm_store, project_manager=self.project_manager
        )
        self.knowledge_manager = knowledge_manager or KnowledgeManager(
            store=pm_store, project_manager=self.project_manager
        )
        self.agent_binder = agent_binder or AgentBinder(
            store=pm_store, project_manager=self.project_manager, upstream=upstream
        )
        self.rag_coordinator = rag_coordinator or RAGCoordinator(
            store=pm_store, project_manager=self.project_manager, upstream=upstream
        )
        self.cowork_bridge = CoworkBridge(store=pm_store)
        self.upstream = upstream
        self._handlers: dict[str, Callable[..., Awaitable[Any]]] = {
            "project.list": self._list,
            "project.create": self._create,
            "project.get": self._get,
            "project.update": self._update,
            "project.archive": self._archive,
            "project.unarchive": self._unarchive,
            "project.star": self._star,
            "project.delete": self._delete,
            "project.instruction.get": self._instr_get,
            "project.instruction.save": self._instr_save,
            "project.instruction.clear": self._instr_clear,
            "project.instruction.snapshots": self._instr_snapshots,
            "project.instruction.snapshot.restore": self._instr_snap_restore,
            "project.instruction.snapshot.delete": self._instr_snap_delete,
            "project.artifact.migrate": self._artifact_migrate,
            "project.artifact.list": self._artifact_list,
            "project.artifact.remove": self._artifact_remove,
            "project.artifact.export": self._artifact_export,
            "project.chat.list": self._chat_list,
            "project.chat.create": self._chat_create,
            "project.chat.get": self._chat_get,
            "project.chat.update": self._chat_update,
            "project.chat.star": self._chat_star,
            "project.chat.delete": self._chat_delete,
            "project.chat.fork": self._chat_fork,
            "project.chat.move": self._chat_move,
            "project.chat.detach": self._chat_detach,
            "project.chat.snapshot.create": self._chat_snap_create,
            "project.chat.snapshot.list": self._chat_snap_list,
            "project.chat.snapshot.restore": self._chat_snap_restore,
            "project.chat.snapshot.delete": self._chat_snap_delete,
            "project.chat.message.list": self._msg_list,
            "project.chat.message.add": self._msg_add,
            "project.chat.message.delete": self._msg_delete,
            "project.chat.temp_attachment.add": self._temp_attach_add,
            "project.chat.temp_attachment.list": self._temp_attach_list,
            "project.chat.temp_attachment.delete": self._temp_attach_delete,
            "project.knowledge.file.upload": self._kfile_upload,
            "project.knowledge.file.replace": self._kfile_replace,
            "project.knowledge.file.rename": self._kfile_rename,
            "project.knowledge.file.move": self._kfile_move,
            "project.duplicate": self._duplicate,
            "project.audit.list": self._audit_list,
            "project.audit.log": self._audit_log,
            "project.knowledge.folder.list": self._folder_list,
            "project.knowledge.folder.create": self._folder_create,
            "project.knowledge.folder.update": self._folder_update,
            "project.knowledge.folder.delete": self._folder_delete,
            "project.knowledge.file.list": self._kfile_list,
            "project.knowledge.file.get": self._kfile_get,
            "project.knowledge.file.delete": self._kfile_delete,
            "project.knowledge.file.statuses": self._kfile_statuses,
            "project.agent.get": self._agent_get,
            "project.agent.set": self._agent_set,
            "project.agent.remove": self._agent_remove,
            "project.agent.list": self._agent_list,
            "project.agent.preview": self._agent_preview,
            "project.agent.system_prompt": self._agent_system_prompt,
            "project.rag.index_file": self._rag_index_file,
            "project.rag.index_folder": self._rag_index_folder,
            "project.rag.query": self._rag_query,
            "project.rag.remove_index": self._rag_remove_index,
            "project.rag.status": self._rag_status,
            "project.rag.config.get": self._rag_config_get,
            "project.rag.config.set": self._rag_config_set,
            "project.upstream.health": self._upstream_health,
            "project.upstream.circuits": self._upstream_circuits,
            "cowork.trigger": self._cowork_trigger,
            "cowork.status": self._cowork_status,
            "project.export": self._export_project,
        }

    # ── Project handlers ──

    async def _list(self, params: Any) -> list[dict]:
        params = params or {}
        rows = await self.project_manager.list(
            include_archived=bool(params.get("include_archived", False)),
            only_starred=bool(params.get("only_starred", False)),
        )
        return [r.model_dump() for r in rows]

    async def _create(self, params: Any) -> dict:
        payload = ProjectCreate(**(params or {}))
        proj = await self.project_manager.create(payload)
        return proj.model_dump()

    async def _get(self, params: Any) -> dict:
        proj = await self.project_manager.get(params["project_id"])
        return proj.model_dump()

    async def _update(self, params: Any) -> dict:
        payload = ProjectUpdate(**(params.get("fields") or {}))
        proj = await self.project_manager.update(params["project_id"], payload)
        return proj.model_dump()

    async def _archive(self, params: Any) -> dict:
        proj = await self.project_manager.archive(params["project_id"])
        return proj.model_dump()

    async def _unarchive(self, params: Any) -> dict:
        proj = await self.project_manager.unarchive(params["project_id"])
        return proj.model_dump()

    async def _star(self, params: Any) -> dict:
        proj = await self.project_manager.star(
            params["project_id"], bool(params.get("starred", True))
        )
        return proj.model_dump()

    async def _delete(self, params: Any) -> dict:
        await self.project_manager.delete(params["project_id"])
        return {"deleted": True}

    async def _instr_get(self, params: Any) -> dict:
        ic = await self.instruction_engine.get(params["project_id"])
        return ic.model_dump()

    async def _instr_save(self, params: Any) -> dict:
        payload = InstructionSave(content=params["content"])
        ic = await self.instruction_engine.save(params["project_id"], payload)
        return ic.model_dump()

    async def _instr_clear(self, params: Any) -> dict:
        ok = await self.instruction_engine.clear(params["project_id"])
        return {"cleared": ok}

    async def _instr_snapshots(self, params: Any) -> list[dict]:
        rows = await self.instruction_engine.list_snapshots(params["project_id"])
        return [r.model_dump() for r in rows]

    async def _instr_snap_restore(self, params: Any) -> dict:
        ic = await self.instruction_engine.restore_snapshot(params["snapshot_id"])
        return ic.model_dump()

    async def _instr_snap_delete(self, params: Any) -> dict:
        ok = await self.instruction_engine.delete_snapshot(params["snapshot_id"])
        return {"deleted": ok}

    async def _artifact_migrate(self, params: Any) -> dict:
        ref = await self.project_manager.migrate_artifact(
            params["project_id"], params["artifact_id"]
        )
        return ref.model_dump()

    async def _artifact_list(self, params: Any) -> list[dict]:
        params = params or {}
        refs = await self.project_manager.list_artifacts(
            params["project_id"],
            artifact_type=params.get("type"),
            artifact_kind=params.get("kind"),
            search=params.get("search"),
        )
        return [r.model_dump() for r in refs]

    async def _artifact_remove(self, params: Any) -> dict:
        ok = await self.project_manager.remove_artifact(params["artifact_id"])
        return {"removed": ok}

    async def _artifact_export(self, params: Any) -> dict:
        params = params or {}
        import base64

        data = await self.project_manager.export_artifacts(
            params["project_id"],
            artifact_ids=params.get("artifact_ids"),
        )
        return {"zip_base64": base64.b64encode(data).decode("ascii"), "size": len(data)}

    # ── Chat handlers ──

    async def _chat_list(self, params: Any) -> list[dict]:
        params = params or {}
        chats = await self.chat_manager.list_chats(
            params["project_id"], only_starred=bool(params.get("only_starred", False))
        )
        return [c.model_dump() for c in chats]

    async def _chat_create(self, params: Any) -> dict:
        payload = ChatCreate(**(params or {}))
        chat = await self.chat_manager.create_chat(params["project_id"], payload)
        return chat.model_dump()

    async def _chat_get(self, params: Any) -> dict:
        chat = await self.chat_manager.get_chat(params["chat_id"])
        return chat.model_dump()

    async def _chat_update(self, params: Any) -> dict:
        payload = ChatUpdate(**(params.get("fields") or {}))
        fields = payload.model_dump(exclude_unset=True)
        chat = await self.chat_manager.update_chat(params["chat_id"], fields)
        return chat.model_dump()

    async def _chat_star(self, params: Any) -> dict:
        chat = await self.chat_manager.star_chat(
            params["chat_id"], bool(params.get("starred", True))
        )
        return chat.model_dump()

    async def _chat_delete(self, params: Any) -> dict:
        await self.chat_manager.delete_chat(params["chat_id"])
        return {"deleted": True}

    async def _chat_fork(self, params: Any) -> dict:
        params = params or {}
        chat = await self.chat_manager.fork_chat(params["chat_id"], label=params.get("label"))
        return chat.model_dump()

    async def _chat_move(self, params: Any) -> dict:
        chat = await self.chat_manager.move_chat(
            params["chat_id"], params["target_project_id"]
        )
        return chat.model_dump()

    async def _chat_detach(self, params: Any) -> dict:
        chat = await self.chat_manager.detach_chat(params["chat_id"])
        return chat.model_dump()

    async def _chat_snap_create(self, params: Any) -> dict:
        params = params or {}
        snap = await self.chat_manager.create_snapshot(
            params["chat_id"], label=params.get("label")
        )
        return snap.model_dump()

    async def _chat_snap_list(self, params: Any) -> list[dict]:
        snaps = await self.chat_manager.list_snapshots(params["chat_id"])
        return [s.model_dump() for s in snaps]

    async def _chat_snap_restore(self, params: Any) -> dict:
        chat = await self.chat_manager.restore_snapshot(params["snapshot_id"])
        return chat.model_dump()

    async def _chat_snap_delete(self, params: Any) -> dict:
        await self.chat_manager.delete_snapshot(params["snapshot_id"])
        return {"deleted": True}

    async def _msg_list(self, params: Any) -> list[dict]:
        params = params or {}
        msgs = await self.chat_manager.list_messages(
            params["chat_id"],
            limit=params.get("limit", 100),
            offset=params.get("offset", 0),
        )
        return [m.model_dump() for m in msgs]

    async def _msg_add(self, params: Any) -> dict:
        payload = MessageCreate(
            content=params["content"],
            rag_mode=params.get("rag_mode"),
            rag_scope=params.get("rag_scope"),
            temp_file_ids=params.get("temp_file_ids"),
        )
        msg = await self.chat_manager.add_message(params["chat_id"], payload)
        return msg.model_dump()

    async def _msg_delete(self, params: Any) -> dict:
        await self.chat_manager.delete_message(params["message_id"])
        return {"deleted": True}

    async def _temp_attach_add(self, params: Any) -> dict:
        ta = await self.chat_manager.add_temp_attachment(
            params["chat_id"],
            params["file_path"],
            original_name=params["original_name"],
            file_size=params.get("file_size", 0),
            mime_type=params.get("mime_type"),
        )
        return ta.model_dump()

    async def _temp_attach_list(self, params: Any) -> list[dict]:
        tas = await self.chat_manager.list_temp_attachments(params["chat_id"])
        return [t.model_dump() for t in tas]

    async def _temp_attach_delete(self, params: Any) -> dict:
        ok = await self.chat_manager.delete_temp_attachment(params["attachment_id"])
        return {"deleted": ok}

    async def _kfile_upload(self, params: Any) -> dict:
        kfile = await self.knowledge_manager.upload_file(
            params["project_id"],
            params["source_path"],
            original_name=params["original_name"],
            folder_id=params.get("folder_id"),
            mime_type=params.get("mime_type"),
        )
        return kfile.model_dump()

    async def _kfile_replace(self, params: Any) -> dict:
        kfile = await self.knowledge_manager.replace_file(
            params["file_id"], params["source_path"]
        )
        return kfile.model_dump()

    async def _kfile_rename(self, params: Any) -> dict:
        kfile = await self.knowledge_manager.rename_file(
            params["file_id"], params["name"]
        )
        return kfile.model_dump()

    async def _kfile_move(self, params: Any) -> dict:
        kfile = await self.knowledge_manager.move_file(
            params["file_id"], params.get("folder_id")
        )
        return kfile.model_dump()

    async def _duplicate(self, params: Any) -> dict:
        proj = await self.project_manager.duplicate_project(
            params["project_id"], name=params.get("name")
        )
        return proj.model_dump()

    async def _audit_list(self, params: Any) -> list[dict]:
        params = params or {}
        rows = self.project_manager.store.list_audit_log(
            params["project_id"],
            limit=params.get("limit", 100),
            offset=params.get("offset", 0),
        )
        return [AuditLogEntry.from_row(r).model_dump() for r in rows]

    async def _audit_log(self, params: Any) -> dict:
        row = self.project_manager.store.create_audit_log({
            "project_id": params["project_id"],
            "chat_id": params.get("chat_id"),
            "action": params["action"],
            "agent_id": params.get("agent_id"),
            "details": params.get("details"),
        })
        return AuditLogEntry.from_row(row).model_dump()

    # ── Knowledge handlers ──

    async def _folder_list(self, params: Any) -> list[dict]:
        params = params or {}
        folders = await self.knowledge_manager.list_folders(
            params["project_id"], parent_id=params.get("parent_id")
        )
        return [f.model_dump() for f in folders]

    async def _folder_create(self, params: Any) -> dict:
        payload = FolderCreate(name=params["name"], parent_id=params.get("parent_id"))
        folder = await self.knowledge_manager.create_folder(params["project_id"], payload)
        return folder.model_dump()

    async def _folder_update(self, params: Any) -> dict:
        payload = FolderUpdate(name=params.get("name"), parent_id=params.get("parent_id"))
        folder = await self.knowledge_manager.update_folder(params["folder_id"], payload)
        return folder.model_dump()

    async def _folder_delete(self, params: Any) -> dict:
        await self.knowledge_manager.delete_folder(params["folder_id"])
        return {"deleted": True}

    async def _kfile_list(self, params: Any) -> list[dict]:
        params = params or {}
        files = await self.knowledge_manager.list_files(
            params["project_id"], folder_id=params.get("folder_id")
        )
        return [f.model_dump() for f in files]

    async def _kfile_get(self, params: Any) -> dict:
        kfile = await self.knowledge_manager.get_file(params["file_id"])
        return kfile.model_dump()

    async def _kfile_delete(self, params: Any) -> dict:
        await self.knowledge_manager.delete_file(params["file_id"])
        return {"deleted": True}

    async def _kfile_statuses(self, params: Any) -> list[dict]:
        statuses = await self.knowledge_manager.list_file_statuses(params["project_id"])
        return [s.model_dump() for s in statuses]

    # ── Agent handlers ──

    async def _agent_get(self, params: Any) -> dict:
        params = params or {}
        binding = await self.agent_binder.get_binding(
            params["project_id"], chat_id=params.get("chat_id")
        )
        return binding.model_dump()

    async def _agent_set(self, params: Any) -> dict:
        params = params or {}
        merge_mode = None
        if params.get("merge_mode"):
            merge_mode = PromptMergeMode(params["merge_mode"])
        binding = await self.agent_binder.set_binding(
            params["project_id"],
            agent_id=params.get("agent_id"),
            merge_mode=merge_mode,
            chat_id=params.get("chat_id"),
        )
        return binding.model_dump()

    async def _agent_remove(self, params: Any) -> dict:
        params = params or {}
        await self.agent_binder.remove_binding(
            params["project_id"], chat_id=params.get("chat_id")
        )
        return {"removed": True}

    async def _agent_list(self, params: Any) -> list[dict]:
        agents = await self.agent_binder.list_available_agents()
        return [a.model_dump() for a in agents]

    async def _agent_preview(self, params: Any) -> dict:
        preview = await self.agent_binder.get_agent_preview(params["agent_id"])
        if not preview:
            raise AgentBinderError(f"agent unavailable: {params['agent_id']}")
        return preview.model_dump()

    async def _agent_system_prompt(self, params: Any) -> dict:
        params = params or {}
        prompt = await self.agent_binder.build_system_prompt(
            params["project_id"],
            agent_prompt=params.get("agent_prompt"),
            chat_id=params.get("chat_id"),
        )
        return {"system_prompt": prompt}

    # ── RAG handlers ──

    async def _rag_index_file(self, params: Any) -> dict:
        result = await self.rag_coordinator.index_file(params["file_id"])
        return result

    async def _rag_index_folder(self, params: Any) -> dict:
        results = await self.rag_coordinator.index_folder(params["folder_id"])
        return {"indexed": len(results), "results": results}

    async def _rag_query(self, params: Any) -> dict:
        params = params or {}
        result = await self.rag_coordinator.query(
            params["project_id"],
            params["query"],
            mode=params.get("mode"),
            folder_ids=params.get("folder_ids"),
            top_k=params.get("top_k"),
            threshold=params.get("threshold"),
            chat_id=params.get("chat_id"),
        )
        return result

    async def _rag_remove_index(self, params: Any) -> dict:
        result = await self.rag_coordinator.remove_file_index(params["file_id"])
        return result

    async def _rag_status(self, params: Any) -> dict:
        status = await self.rag_coordinator.get_rag_status(params["project_id"])
        return status

    async def _rag_config_get(self, params: Any) -> dict:
        proj = await self.project_manager.get(params["project_id"])
        return {
            "rag_mode": proj.rag_mode,
            "rag_top_k": proj.rag_top_k,
            "rag_threshold": proj.rag_threshold,
        }

    async def _rag_config_set(self, params: Any) -> dict:
        fields = {}
        if "rag_mode" in params:
            fields["rag_mode"] = params["rag_mode"]
        if "rag_top_k" in params:
            fields["rag_top_k"] = params["rag_top_k"]
        if "rag_threshold" in params:
            fields["rag_threshold"] = params["rag_threshold"]
        payload = ProjectUpdate(**fields)
        proj = await self.project_manager.update(params["project_id"], payload)
        return {
            "rag_mode": proj.rag_mode,
            "rag_top_k": proj.rag_top_k,
            "rag_threshold": proj.rag_threshold,
        }

    # ── Upstream handlers ──

    async def _upstream_health(self, params: Any) -> dict:
        return await self.upstream.health_check_all()

    async def _upstream_circuits(self, params: Any) -> dict:
        return self.upstream.get_circuit_status()

    # ── Cowork handlers ──

    async def _cowork_trigger(self, params: Any) -> dict:
        trigger = CoworkTrigger(**params)
        task = await self.cowork_bridge.trigger_task(trigger)
        return task.model_dump()

    async def _cowork_status(self, params: Any) -> dict:
        task = await self.cowork_bridge.get_status(params["task_id"])
        return task.model_dump()

    # ── Export handler ──

    async def _export_project(self, params: Any) -> dict:
        import base64
        data = await self.project_manager.export_project(params["project_id"])
        return {"zip_base64": base64.b64encode(data).decode("ascii"), "size": len(data)}

    # ── Dispatch ──

    async def dispatch(self, method: str, params: Any) -> Any:
        handler = self._handlers.get(method)
        if handler is None:
            raise ValueError("unknown method: " + method)
        return await handler(params)

    async def handle_request(self, raw: bytes) -> bytes:
        try:
            req = json.loads(raw.decode("utf-8"))
        except Exception as e:
            return _error(None, -32700, "parse error: " + str(e))
        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params")
        handler = self._handlers.get(method) if isinstance(method, str) else None
        if handler is None:
            return _error(req_id, -32601, "method not found: " + str(method))
        try:
            result = await handler(params)
            return _result(req_id, result)
        except ProjectNotFound as e:
            return _error(req_id, -32001, "project not found: " + str(e))
        except ProjectNotArchived as e:
            return _error(req_id, -32002, "project not archived: " + str(e))
        except ArtifactAlreadyMigrated as e:
            return _error(req_id, -32003, "artifact already migrated: " + str(e))
        except ArtifactNotFound as e:
            return _error(req_id, -32004, "artifact not found: " + str(e))
        except ChatNotFound as e:
            return _error(req_id, -32005, "chat not found: " + str(e))
        except FolderNotFound as e:
            return _error(req_id, -32006, "folder not found: " + str(e))
        except KnowledgeFileNotFound as e:
            return _error(req_id, -32007, "knowledge file not found: " + str(e))
        except SnapshotNotFound as e:
            return _error(req_id, -32010, "snapshot not found: " + str(e))
        except AgentBinderError as e:
            return _error(req_id, -32008, "agent binder error: " + str(e))
        except RAGError as e:
            return _error(req_id, -32009, "rag error: " + str(e))
        except CoworkTaskNotFound as e:
            return _error(req_id, -32011, "cowork task not found: " + str(e))
        except ProjectError as e:
            return _error(req_id, -32000, "project error: " + str(e))
        except ValidationError as e:
            return _error(req_id, -32602, "invalid params: " + str(e.errors()))
        except KeyError as e:
            return _error(req_id, -32602, "missing param: " + str(e))
        except Exception as e:
            logger.exception("rpc handler failed method=%s", method)
            return _error(req_id, -32603, "internal error: " + str(e))

    async def _client_cb(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        peer = writer.get_extra_info("peername")
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                resp = await self.handle_request(line)
                writer.write(resp + b"\n")
                await writer.drain()
        except Exception:
            logger.exception("client connection error peer=%s", peer)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def serve(self, sock_path: Optional[str] = None) -> None:
        sock_path = sock_path or config.SOCKET_PATH
        if os.path.exists(sock_path):
            os.remove(sock_path)
        server = await asyncio.start_unix_server(self._client_cb, path=sock_path)
        os.chmod(sock_path, 0o666)
        logger.info("ProjectRPCServer listening on %s", sock_path)
        async with server:
            await server.serve_forever()


def _result(req_id: Any, result: Any) -> bytes:
    return json.dumps(
        {"jsonrpc": "2.0", "id": req_id, "result": result}
    ).encode("utf-8")


def _error(req_id: Any, code: int, message: str) -> bytes:
    return json.dumps(
        {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
    ).encode("utf-8")


async def run_daemon(sock_path: Optional[str] = None) -> None:
    config.ensure_dirs()
    server = ProjectRPCServer()
    await server.serve(sock_path)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    try:
        asyncio.run(run_daemon())
    except KeyboardInterrupt:
        logger.info("daemon interrupted, shutting down")


if __name__ == "__main__":
    main()
