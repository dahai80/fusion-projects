import asyncio
import json
import logging
from typing import Any, Optional

from project_service import config

logger = logging.getLogger(__name__)


class RPCError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__("[{}] {}".format(code, message))
        self.code = code
        self.message = message


class ProjectClient:
    def __init__(self, sock_path: Optional[str] = None) -> None:
        self.sock_path = sock_path or config.SOCKET_PATH

    async def call(
        self,
        method: str,
        params: Any = None,
        timeout: float = 10.0,
    ) -> Any:
        req = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        reader, writer = await asyncio.open_unix_connection(self.sock_path)
        try:
            writer.write((json.dumps(req) + "\n").encode("utf-8"))
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), timeout=timeout)
            if not line:
                raise ConnectionError("empty response from " + self.sock_path)
            resp = json.loads(line.decode("utf-8"))
            if "error" in resp:
                err = resp["error"]
                raise RPCError(err.get("code"), err.get("message"))
            return resp.get("result")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def list_projects(self, **kw: Any) -> Any:
        return await self.call("project.list", kw)

    async def create_project(self, **kw: Any) -> Any:
        return await self.call("project.create", kw)

    async def get_project(self, project_id: str) -> Any:
        return await self.call("project.get", {"project_id": project_id})

    async def update_project(self, project_id: str, **fields: Any) -> Any:
        return await self.call(
            "project.update", {"project_id": project_id, "fields": fields}
        )

    async def archive_project(self, project_id: str) -> Any:
        return await self.call("project.archive", {"project_id": project_id})

    async def unarchive_project(self, project_id: str) -> Any:
        return await self.call("project.unarchive", {"project_id": project_id})

    async def star_project(self, project_id: str, starred: bool = True) -> Any:
        return await self.call(
            "project.star", {"project_id": project_id, "starred": starred}
        )

    async def delete_project(self, project_id: str) -> Any:
        return await self.call("project.delete", {"project_id": project_id})

    async def get_instruction(self, project_id: str) -> Any:
        return await self.call("project.instruction.get", {"project_id": project_id})

    async def save_instruction(self, project_id: str, content: str) -> Any:
        return await self.call(
            "project.instruction.save",
            {"project_id": project_id, "content": content},
        )

    async def clear_instruction(self, project_id: str) -> Any:
        return await self.call(
            "project.instruction.clear", {"project_id": project_id}
        )

    async def list_instruction_snapshots(self, project_id: str) -> Any:
        return await self.call(
            "project.instruction.snapshots", {"project_id": project_id}
        )
