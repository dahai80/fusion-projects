
from pydantic import BaseModel, Field


class ChatCreate(BaseModel):
    title: str | None = None
    agent_id: str | None = None


class ChatUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    is_starred: bool | None = None
    agent_id: str | None = None


class Chat(BaseModel):
    id: str
    project_id: str | None = None
    title: str | None = None
    is_starred: bool = False
    agent_id: str | None = None
    fork_from_chat_id: str | None = None
    fork_from_snapshot_id: str | None = None
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
    project_id: str | None = None
    title: str | None = None
    is_starred: bool = False
    agent_id: str | None = None
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
    title: str | None = None
    messages: str = "[]"
    instruction_snapshot_id: str | None = None
    message_count: int = 0
    agent_id: str | None = None
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
    label: str | None = None


class ChatMoveRequest(BaseModel):
    target_project_id: str


class MessageCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=200000)
    role: str = "user"
    rag_mode: str | None = None
    rag_scope: list[str] | None = None
    model: str | None = None
    temperature: float | None = Field(None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(None, ge=1, le=32768)
    stream: bool | None = None


class TempAttachment(BaseModel):
    id: str
    chat_id: str
    file_path: str
    original_name: str
    file_size: int = 0
    mime_type: str | None = None
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
