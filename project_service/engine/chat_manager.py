import asyncio
import json
import logging
import shutil
import uuid
from pathlib import Path
from typing import Optional

from project_service.engine.knowledge_manager import KnowledgeError, _sanitize_name, _validate_source
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
from project_service.store.file_store import FileStore
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
        file_store: Optional[FileStore] = None,
    ) -> None:
        self.store = store or ProjectStore()
        self.project_manager = project_manager or ProjectManager()
        self.file_store = file_store or FileStore()

    async def _ensure_project(self, project_id: str) -> None:
        row = self.store.get_project(project_id)
        if not row:
            raise ProjectNotFound(project_id)

    async def _assert_chat_in_project(self, chat_id: str, project_id: Optional[str]) -> Chat:
        chat = await self.get_chat(chat_id)
        if project_id is not None and chat.project_id is not None and chat.project_id != project_id:
            logger.warning("chat ownership mismatch chat=%s chat_project=%s path_project=%s", chat_id, chat.project_id, project_id)
            raise ChatNotFound(chat_id)
        return chat

    async def create_chat(self, project_id: str, payload: ChatCreate) -> Chat:
        await self._ensure_project(project_id)
        data = payload.model_dump()
        data["project_id"] = project_id
        row = self.store.create_chat(data)
        logger.info("chat created id=%s project=%s", row["id"], project_id)
        return Chat.from_row(row)

    async def get_chat(self, chat_id: str, project_id: Optional[str] = None) -> Chat:
        row = self.store.get_chat(chat_id)
        if not row:
            raise ChatNotFound(chat_id)
        chat = Chat.from_row(row)
        if project_id is not None and chat.project_id is not None and chat.project_id != project_id:
            logger.warning("get_chat ownership mismatch chat=%s chat_project=%s req_project=%s", chat_id, chat.project_id, project_id)
            raise ChatNotFound(chat_id)
        return chat

    async def list_chats(
        self,
        project_id: str,
        only_starred: bool = False,
    ) -> list[ChatListItem]:
        await self._ensure_project(project_id)
        rows = self.store.list_chats(project_id, only_starred=only_starred)
        return [ChatListItem.from_row(r) for r in rows]

    async def update_chat(self, chat_id: str, fields: dict, project_id: Optional[str] = None) -> Chat:
        await self._assert_chat_in_project(chat_id, project_id)
        row = self.store.update_chat(chat_id, fields)
        if not row:
            raise ChatNotFound(chat_id)
        logger.info("chat updated id=%s fields=%s", chat_id, list(fields.keys()))
        return Chat.from_row(row)

    async def star_chat(self, chat_id: str, starred: bool = True, project_id: Optional[str] = None) -> Chat:
        return await self.update_chat(chat_id, {"is_starred": starred}, project_id=project_id)

    async def delete_chat(self, chat_id: str, project_id: Optional[str] = None) -> None:
        chat = await self._assert_chat_in_project(chat_id, project_id)
        if not self.store.delete_chat(chat_id):
            raise ChatNotFound(chat_id)
        if chat.project_id:
            attach_dir = self.file_store.project_dir(chat.project_id) / "attachments" / chat_id
            try:
                if attach_dir.exists():
                    shutil.rmtree(attach_dir)
                    logger.info("chat attachments dir removed id=%s dir=%s", chat_id, attach_dir)
            except OSError as e:
                logger.warning("chat attachments dir cleanup failed id=%s dir=%s err=%s", chat_id, attach_dir, e)
        logger.info("chat deleted id=%s", chat_id)

    async def move_chat(self, chat_id: str, target_project_id: str, project_id: Optional[str] = None) -> Chat:
        await self._assert_chat_in_project(chat_id, project_id)
        await self._ensure_project(target_project_id)
        chat = await self.get_chat(chat_id)
        row = self.store.update_chat(chat_id, {"project_id": target_project_id})
        if not row:
            raise ChatNotFound(chat_id)
        logger.info("chat moved id=%s from=%s to=%s", chat_id, chat.project_id, target_project_id)
        return Chat.from_row(row)

    async def detach_chat(self, chat_id: str, project_id: Optional[str] = None) -> Chat:
        chat = await self._assert_chat_in_project(chat_id, project_id)
        row = self.store.detach_chat(chat_id)
        if not row:
            raise ChatNotFound(chat_id)
        logger.info("chat detached id=%s from_project=%s", chat_id, chat.project_id)
        return Chat.from_row(row)

    async def fork_chat(
        self,
        chat_id: str,
        label: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> Chat:
        source = await self._assert_chat_in_project(chat_id, project_id)
        snapshot = await self.create_snapshot(chat_id)
        fork_data = {
            "project_id": source.project_id,
            "title": label or f"Fork of {source.title or 'Chat'}",
            "agent_id": source.agent_id,
            "fork_from_chat_id": chat_id,
            "fork_from_snapshot_id": snapshot.id,
        }
        row = self.store.create_chat(fork_data)
        source_msgs = await asyncio.to_thread(self.store.list_messages, chat_id, limit=10000)
        await asyncio.to_thread(self.store.create_messages_batch, row["id"], source_msgs)
        logger.info("chat forked from=%s to=%s snapshot=%s msgs=%d", chat_id, row["id"], snapshot.id, len(source_msgs))
        return Chat.from_row(row)

    async def create_snapshot(
        self,
        chat_id: str,
        label: Optional[str] = None,
        instruction_snapshot_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> ChatSnapshot:
        chat = await self._assert_chat_in_project(chat_id, project_id)
        msg_count = self.store.count_messages(chat_id)
        messages_json = self.store.dump_chat_messages(chat_id)
        data = {
            "chat_id": chat_id,
            "title": label or chat.title,
            "messages": messages_json,
            "instruction_snapshot_id": instruction_snapshot_id,
            "message_count": msg_count,
            "agent_id": chat.agent_id,
        }
        row = self.store.create_chat_snapshot(data)
        logger.info("chat snapshot created id=%s chat=%s msgs=%d", row["id"], chat_id, msg_count)
        return ChatSnapshot.from_row(row)

    async def list_snapshots(self, chat_id: str, project_id: Optional[str] = None) -> list[ChatSnapshot]:
        await self._assert_chat_in_project(chat_id, project_id)
        rows = self.store.list_chat_snapshots(chat_id)
        return [ChatSnapshot.from_row(r) for r in rows]

    async def restore_snapshot(self, snapshot_id: str, project_id: Optional[str] = None) -> Chat:
        snap_row = self.store.get_chat_snapshot(snapshot_id)
        if not snap_row:
            raise ChatNotFound(f"snapshot {snapshot_id}")
        chat_id = snap_row["chat_id"]
        await self._assert_chat_in_project(chat_id, project_id)
        try:
            messages_json = snap_row.get("messages") or "[]"
            restored_rows = json.loads(messages_json)
        except (ValueError, TypeError) as e:
            logger.error("snapshot %s messages decode failed: %s", snapshot_id, e)
            raise ChatError(f"snapshot {snapshot_id} has corrupt messages payload")
        self.store.replace_chat_messages(chat_id, restored_rows)
        logger.info("restored snapshot %s for chat %s msgs=%d", snapshot_id, chat_id, len(restored_rows))
        return await self.get_chat(chat_id)

    async def delete_snapshot(self, snapshot_id: str, project_id: Optional[str] = None) -> None:
        snap_row = self.store.get_chat_snapshot(snapshot_id)
        if not snap_row:
            raise ChatNotFound(f"snapshot {snapshot_id}")
        await self._assert_chat_in_project(snap_row["chat_id"], project_id)
        if not self.store.delete_chat_snapshot(snapshot_id):
            raise ChatNotFound(f"snapshot {snapshot_id}")
        logger.info("chat snapshot deleted id=%s", snapshot_id)

    async def add_message(self, chat_id: str, payload: MessageCreate, project_id: Optional[str] = None) -> Message:
        await self._assert_chat_in_project(chat_id, project_id)
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
        project_id: Optional[str] = None,
    ) -> list[Message]:
        await self._assert_chat_in_project(chat_id, project_id)
        rows = self.store.list_messages(chat_id, limit=limit, offset=offset)
        return [Message.from_row(r) for r in rows]

    async def delete_message(self, message_id: str, project_id: Optional[str] = None) -> None:
        msg = self.store.get_message(message_id)
        if not msg:
            raise ChatNotFound(f"message {message_id}")
        await self._assert_chat_in_project(msg["chat_id"], project_id)
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
        project_id: Optional[str] = None,
    ) -> TempAttachment:
        chat = await self._assert_chat_in_project(chat_id, project_id)
        src = _validate_source(file_path)
        safe_name = _sanitize_name(original_name)
        chat_project_id = chat.project_id
        base_dir = self.file_store.project_dir(chat_project_id) / "attachments" if chat_project_id else Path(self.file_store.storage_dir) / "_detached"
        dest_dir = base_dir / chat_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = (dest_dir / safe_name).resolve()
        if not dest_path.is_relative_to(dest_dir.resolve()):
            raise ChatError("path traversal in original_name")
        if dest_path.exists():
            dest_path = dest_dir / f"{dest_path.stem}_{uuid.uuid4().hex[:8]}{dest_path.suffix}"
        shutil.copy2(str(src), str(dest_path))
        data = {
            "chat_id": chat_id,
            "file_path": str(dest_path),
            "original_name": safe_name,
            "file_size": src.stat().st_size,
            "mime_type": mime_type,
        }
        row = self.store.create_temp_attachment(data)
        logger.info("temp attachment added id=%s chat=%s name=%s", row["id"], chat_id, safe_name)
        return TempAttachment.from_row(row)

    async def list_temp_attachments(self, chat_id: str, project_id: Optional[str] = None) -> list[TempAttachment]:
        await self._assert_chat_in_project(chat_id, project_id)
        rows = self.store.list_temp_attachments(chat_id)
        return [TempAttachment.from_row(r) for r in rows]

    async def delete_temp_attachment(self, attachment_id: str, project_id: Optional[str] = None) -> bool:
        existing = self.store.get_temp_attachment(attachment_id)
        if not existing:
            raise ChatNotFound(f"temp attachment {attachment_id}")
        await self._assert_chat_in_project(existing["chat_id"], project_id)
        old_path = Path(existing["file_path"])
        try:
            if old_path.exists():
                old_path.unlink()
        except OSError as e:
            logger.warning("temp attachment unlink failed id=%s path=%s err=%s", attachment_id, old_path, e)
        deleted = self.store.delete_temp_attachment(attachment_id)
        logger.info("temp attachment deleted id=%s deleted=%s", attachment_id, deleted)
        return deleted
