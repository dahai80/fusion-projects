import asyncio
import json
import logging
import os
from typing import Any, Awaitable, Callable, Optional

from pydantic import ValidationError

from project_service import config
from project_service.engine.instruction_engine import InstructionEngine
from project_service.engine.project_manager import (
    ArtifactAlreadyMigrated,
    ArtifactNotFound,
    ProjectError,
    ProjectNotArchived,
    ProjectNotFound,
    ProjectManager,
)
from project_service.models.instruction import InstructionSave
from project_service.models.project import ProjectCreate, ProjectUpdate

logger = logging.getLogger(__name__)


class ProjectRPCServer:
    NAMESPACE = "project"

    def __init__(
        self,
        project_manager: Optional[ProjectManager] = None,
        instruction_engine: Optional[InstructionEngine] = None,
    ) -> None:
        self.project_manager = project_manager or ProjectManager()
        self.instruction_engine = instruction_engine or InstructionEngine(
            project_manager=self.project_manager
        )
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
            "project.artifact.migrate": self._artifact_migrate,
            "project.artifact.list": self._artifact_list,
            "project.artifact.remove": self._artifact_remove,
        }

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

    async def _artifact_migrate(self, params: Any) -> dict:
        ref = await self.project_manager.migrate_artifact(
            params["project_id"], params["artifact_id"]
        )
        return ref.model_dump()

    async def _artifact_list(self, params: Any) -> list[dict]:
        refs = await self.project_manager.list_artifacts(params["project_id"])
        return [r.model_dump() for r in refs]

    async def _artifact_remove(self, params: Any) -> dict:
        ok = await self.project_manager.remove_artifact(params["artifact_id"])
        return {"removed": ok}

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
