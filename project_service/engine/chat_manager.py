import logging
from typing import Optional

from project_service.engine.project_manager import ProjectManager, ProjectNotFound
from project_service.models.chat import (
    Chat,
    ChatCreate,
    ChatListItem,
    ChatSnapshot,
    Message,
    MessageCreate,
    TempAttachment,
)
from project_service.store.project_store import ProjectStore

logger = logging.getLogger(__name__)


class ChatError(Exception):
    pass


class ChatNotFound(ChatError):
    pass


class ChatManager:
    def __init__(
        self,
        store: Optional[ProjectStore] = None,
        project_manager: Optional[ProjectManager] = None,
    ) -> None:
        self.store = store or ProjectStore()
        self.project_manager = project_manager or ProjectManager()

    async def _ensure_project(self, project_id: str) -> None:
        row = self.store.get_project(project_id)
        if not row:
            raise ProjectNotFound(project_id)

    async def create_chat(self, project_id: str, payload: ChatCreate) -> Chat:
        await self._ensure_project(project_id)
        data = payload.model_dump()
        data["project_id"] = project_id
        row = self.store.create_chat(data)
        logger.info("chat created id=%s project=%s", row["id"], project_id)
        return Chat.from_row(row)

    async def get_chat(self, chat_id: str) -> Chat:
        row = self.store.get_chat(chat_id)
        if not row:
            raise ChatNotFound(chat_id)
        return Chat.from_row(row)

    async def list_chats(
        self,
        project_id: str,
        only_starred: bool = False,
    ) -> list[ChatListItem]:
        await self._ensure_project(project_id)
        rows = self.store.list_chats(project_id, only_starred=only_starred)
        return [ChatListItem.from_row(r) for r in rows]

    async def update_chat(self, chat_id: str, fields: dict) -> Chat:
        row = self.store.update_chat(chat_id, fields)
        if not row:
            raise ChatNotFound(chat_id)
        logger.info("chat updated id=%s fields=%s", chat_id, list(fields.keys()))
        return Chat.from_row(row)

    async def star_chat(self, chat_id: str, starred: bool = True) -> Chat:
        return await self.update_chat(chat_id, {"is_starred": starred})

    async def delete_chat(self, chat_id: str) -> None:
        if not self.store.delete_chat(chat_id):
            raise ChatNotFound(chat_id)
        logger.info("chat deleted id=%s", chat_id)

    async def move_chat(self, chat_id: str, target_project_id: str) -> Chat:
        await self._ensure_project(target_project_id)
        chat = await self.get_chat(chat_id)
        row = self.store.update_chat(chat_id, {"project_id": target_project_id})
        if not row:
            raise ChatNotFound(chat_id)
        logger.info("chat moved id=%s from=%s to=%s", chat_id, chat.project_id, target_project_id)
        return Chat.from_row(row)

    async def detach_chat(self, chat_id: str) -> Chat:
        chat = await self.get_chat(chat_id)
        row = self.store.detach_chat(chat_id)
        if not row:
            raise ChatNotFound(chat_id)
        logger.info("chat detached id=%s from_project=%s", chat_id, chat.project_id)
        return Chat.from_row(row)

    async def fork_chat(
        self,
        chat_id: str,
        label: Optional[str] = None,
    ) -> Chat:
        source = await self.get_chat(chat_id)
        snapshot = await self.create_snapshot(chat_id)
        fork_data = {
            "project_id": source.project_id,
            "title": label or f"Fork of {source.title or 'Chat'}",
            "agent_id": source.agent_id,
            "fork_from_chat_id": chat_id,
            "fork_from_snapshot_id": snapshot.id,
        }
        row = self.store.create_chat(fork_data)
        source_msgs = self.store.list_messages(chat_id, limit=10000)
        for msg_row in source_msgs:
            self.store.create_message({
                "chat_id": row["id"],
                "role": msg_row["role"],
                "content": msg_row["content"],
                "rag_sources": msg_row["rag_sources"],
                "tool_calls": msg_row["tool_calls"],
                "token_usage": msg_row["token_usage"],
            })
        logger.info("chat forked from=%s to=%s snapshot=%s", chat_id, row["id"], snapshot.id)
        return Chat.from_row(row)

    async def create_snapshot(self, chat_id: str, label: Optional[str] = None) -> ChatSnapshot:
        chat = await self.get_chat(chat_id)
        msg_count = self.store.count_messages(chat_id)
        data = {
            "chat_id": chat_id,
            "title": label or chat.title,
            "message_count": msg_count,
            "agent_id": chat.agent_id,
        }
        row = self.store.create_chat_snapshot(data)
        logger.info("chat snapshot created id=%s chat=%s msgs=%d", row["id"], chat_id, msg_count)
        return ChatSnapshot.from_row(row)

    async def list_snapshots(self, chat_id: str) -> list[ChatSnapshot]:
        await self.get_chat(chat_id)
        rows = self.store.list_chat_snapshots(chat_id)
        return [ChatSnapshot.from_row(r) for r in rows]

    async def restore_snapshot(self, snapshot_id: str) -> Chat:
        snap_row = self.store.get_chat_snapshot(snapshot_id)
        if not snap_row:
            raise ChatNotFound(f"snapshot {snapshot_id}")
        chat_id = snap_row["chat_id"]
        chat = await self.get_chat(chat_id)
        logger.info("restoring snapshot %s for chat %s", snapshot_id, chat_id)
        return chat

    async def delete_snapshot(self, snapshot_id: str) -> None:
        if not self.store.delete_chat_snapshot(snapshot_id):
            raise ChatNotFound(f"snapshot {snapshot_id}")
        logger.info("chat snapshot deleted id=%s", snapshot_id)

    async def add_message(self, chat_id: str, payload: MessageCreate) -> Message:
        await self.get_chat(chat_id)
        data = payload.model_dump()
        data["chat_id"] = chat_id
        row = self.store.create_message(data)
        self.store.update_chat(chat_id, {"updated_at": None})
        logger.info("message added id=%s chat=%s role=%s", row["id"], chat_id, data["role"])
        return Message.from_row(row)

    async def list_messages(
        self,
        chat_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Message]:
        await self.get_chat(chat_id)
        rows = self.store.list_messages(chat_id, limit=limit, offset=offset)
        return [Message.from_row(r) for r in rows]

    async def delete_message(self, message_id: str) -> None:
        if not self.store.delete_message(message_id):
            raise ChatNotFound(f"message {message_id}")
        logger.info("message deleted id=%s", message_id)

    async def add_temp_attachment(
        self,
        chat_id: str,
        file_path: str,
        original_name: str,
        file_size: int = 0,
        mime_type: Optional[str] = None,
    ) -> TempAttachment:
        await self.get_chat(chat_id)
        data = {
            "chat_id": chat_id,
            "file_path": file_path,
            "original_name": original_name,
            "file_size": file_size,
            "mime_type": mime_type,
        }
        row = self.store.create_temp_attachment(data)
        logger.info("temp attachment added id=%s chat=%s name=%s", row["id"], chat_id, original_name)
        return TempAttachment.from_row(row)

    async def list_temp_attachments(self, chat_id: str) -> list[TempAttachment]:
        await self.get_chat(chat_id)
        rows = self.store.list_temp_attachments(chat_id)
        return [TempAttachment.from_row(r) for r in rows]

    async def delete_temp_attachment(self, attachment_id: str) -> bool:
        existing = self.store.get_temp_attachment(attachment_id)
        if not existing:
            raise ChatNotFound(f"temp attachment {attachment_id}")
        deleted = self.store.delete_temp_attachment(attachment_id)
        logger.info("temp attachment deleted id=%s deleted=%s", attachment_id, deleted)
        return deleted
