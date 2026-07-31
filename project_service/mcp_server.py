import json
import logging
import os
from typing import Any, Awaitable, Callable, Optional

from project_service.daemon_server import ProjectRPCServer

logger = logging.getLogger(__name__)

MCP_TOOLS = [
    {
        "name": "project_list",
        "description": "List all projects",
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_archived": {"type": "boolean", "default": False},
                "only_starred": {"type": "boolean", "default": False},
            },
        },
    },
    {
        "name": "project_get",
        "description": "Get project details",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "project_search_knowledge",
        "description": "Search project knowledge base via RAG",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "query": {"type": "string"},
                "top_k": {"type": "integer", "default": 5},
            },
            "required": ["project_id", "query"],
        },
    },
    {
        "name": "project_list_knowledge",
        "description": "List knowledge files in a project",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "folder_id": {"type": "string"},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "project_get_instructions",
        "description": "Get project instructions",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "project_list_chats",
        "description": "List chats in a project",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "only_starred": {"type": "boolean", "default": False},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "project_get_chat_messages",
        "description": "Get messages from a chat",
        "inputSchema": {
            "type": "object",
            "properties": {
                "chat_id": {"type": "string"},
                "limit": {"type": "integer", "default": 50},
            },
            "required": ["chat_id"],
        },
    },
]


class MCPServer:
    def __init__(self, rpc_server: Optional[ProjectRPCServer] = None) -> None:
        self.rpc = rpc_server or ProjectRPCServer()
        self._tool_handlers: dict[str, Callable[..., Awaitable[Any]]] = {
            "project_list": self._tool_project_list,
            "project_get": self._tool_project_get,
            "project_search_knowledge": self._tool_search_knowledge,
            "project_list_knowledge": self._tool_list_knowledge,
            "project_get_instructions": self._tool_get_instructions,
            "project_list_chats": self._tool_list_chats,
            "project_get_chat_messages": self._tool_get_chat_messages,
        }

    async def _tool_project_list(self, args: dict) -> list[dict]:
        return await self.rpc.dispatch("project.list", args)

    async def _tool_project_get(self, args: dict) -> dict:
        return await self.rpc.dispatch("project.get", args)

    async def _tool_search_knowledge(self, args: dict) -> dict:
        return await self.rpc.dispatch("project.rag.query", args)

    async def _tool_list_knowledge(self, args: dict) -> list[dict]:
        return await self.rpc.dispatch("project.knowledge.file.list", args)

    async def _tool_get_instructions(self, args: dict) -> dict:
        return await self.rpc.dispatch("project.instruction.get", args)

    async def _tool_list_chats(self, args: dict) -> list[dict]:
        return await self.rpc.dispatch("project.chat.list", args)

    async def _tool_get_chat_messages(self, args: dict) -> dict:
        params = {"chat_id": args["chat_id"], "limit": args.get("limit", 50), "offset": 0}
        return await self.rpc.dispatch("project.chat.message.list", params)

    async def handle_request(self, raw: bytes) -> bytes:
        try:
            req = json.loads(raw.decode("utf-8"))
        except Exception as e:
            return _mcp_error(None, -32700, "parse error: " + str(e))

        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})

        if method == "initialize":
            return _mcp_result(req_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "fusion-project-mcp", "version": "0.1.0"},
            })

        if method == "notifications/initialized":
            return b""

        if method == "tools/list":
            return _mcp_result(req_id, {"tools": MCP_TOOLS})

        if method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})
            handler = self._tool_handlers.get(tool_name)
            if handler is None:
                return _mcp_error(req_id, -32601, "unknown tool: " + tool_name)
            try:
                result = await handler(tool_args)
                return _mcp_result(req_id, {
                    "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                })
            except Exception as e:
                logger.exception("MCP tool failed tool=%s", tool_name)
                return _mcp_result(req_id, {
                    "content": [{"type": "text", "text": json.dumps({"error": str(e)})}],
                    "isError": True,
                })

        return _mcp_error(req_id, -32601, "method not found: " + str(method))


def _mcp_result(req_id: Any, result: Any) -> bytes:
    return json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}).encode("utf-8")


def _mcp_error(req_id: Any, code: int, message: str) -> bytes:
    return json.dumps({
        "jsonrpc": "2.0", "id": req_id,
        "error": {"code": code, "message": message},
    }).encode("utf-8")


async def run_mcp_stdio() -> None:
    import asyncio
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stderr,
    )

    server = MCPServer()
    logger.info("MCP server starting on stdio")

    loop = asyncio.get_event_loop()
    reader = asyncio.StreamReader()

    def _on_stdin_data():
        data = sys.stdin.buffer.readline()
        if data:
            reader.feed_data(data)
        else:
            reader.feed_eof()

    transport = asyncio.SafeStreamReaderProtocol(reader)
    loop.set_reader(sys.stdin.fileno(), _on_stdin_data)

    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            resp = await server.handle_request(line)
            if resp:
                sys.stdout.buffer.write(resp + b"\n")
                sys.stdout.buffer.flush()
    except Exception:
        logger.exception("MCP stdio loop error")
    finally:
        logger.info("MCP server shutting down")


if __name__ == "__main__":
    import asyncio
    asyncio.run(run_mcp_stdio())
