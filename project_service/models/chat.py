from typing import Optional

from pydantic import BaseModel, Field


class ChatCreate(BaseModel):
    title: Optional[str] = None
    agent_id: Optional[str] = None


class ChatUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    is_starred: Optional[bool] = None
    agent_id: Optional[str] = None


class Chat(BaseModel):
    id: str
    project_id: Optional[str] = None
    title: Optional[str] = None
    is_starred: bool = False
    agent_id: Optional[str] = None
    fork_from_chat_id: Optional[str] = None
    fork_from_snapshot_id: Optional[str] = None
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: dict) -> "Chat":
        return cls(
            id=row["id"],
            project_id=row["project_id"] or None,
            title=row["title"],
            is_starred=bool(row["is_starred"]),
            agent_id=row["agent_id"],
            fork_from_chat_id=row["fork_from_chat_id"],
            fork_from_snapshot_id=row["fork_from_snapshot_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class ChatListItem(BaseModel):
    id: str
    project_id: Optional[str] = None
    title: Optional[str] = None
    is_starred: bool = False
    agent_id: Optional[str] = None
    updated_at: str

    @classmethod
    def from_row(cls, row: dict) -> "ChatListItem":
        return cls(
            id=row["id"],
            project_id=row["project_id"] or None,
            title=row["title"],
            is_starred=bool(row["is_starred"]),
            agent_id=row["agent_id"],
            updated_at=row["updated_at"],
        )


class ChatSnapshot(BaseModel):
    id: str
    chat_id: str
    title: Optional[str] = None
    messages: str = "[]"
    instruction_snapshot_id: Optional[str] = None
    message_count: int = 0
    agent_id: Optional[str] = None
    created_at: str

    @classmethod
    def from_row(cls, row: dict) -> "ChatSnapshot":
        return cls(
            id=row["id"],
            chat_id=row["chat_id"],
            title=row["title"],
            messages=row.get("messages", "[]"),
            instruction_snapshot_id=row.get("instruction_snapshot_id"),
            message_count=row["message_count"],
            agent_id=row["agent_id"],
            created_at=row["created_at"],
        )


class ChatForkRequest(BaseModel):
    label: Optional[str] = None


class ChatMoveRequest(BaseModel):
    target_project_id: str


class MessageCreate(BaseModel):
    content: str = Field(..., min_length=1)
    role: str = "user"
    rag_mode: Optional[str] = None
    rag_scope: Optional[list[str]] = None
    temp_file_ids: Optional[list[str]] = None


class TempAttachment(BaseModel):
    id: str
    chat_id: str
    file_path: str
    original_name: str
    file_size: int = 0
    mime_type: Optional[str] = None
    created_at: str

    @classmethod
    def from_row(cls, row: dict) -> "TempAttachment":
        return cls(
            id=row["id"],
            chat_id=row["chat_id"],
            file_path=row["file_path"],
            original_name=row["original_name"],
            file_size=row["file_size"],
            mime_type=row["mime_type"],
            created_at=row["created_at"],
        )


class Message(BaseModel):
    id: str
    chat_id: str
    role: str
    content: str
    created_at: str

    @classmethod
    def from_row(cls, row: dict) -> "Message":
        return cls(
            id=row["id"],
            chat_id=row["chat_id"],
            role=row["role"],
            content=row["content"],
            created_at=row["created_at"],
        )
