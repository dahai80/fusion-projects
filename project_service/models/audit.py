from typing import Optional

from pydantic import BaseModel


class AuditLogEntry(BaseModel):
    id: str
    project_id: str
    chat_id: Optional[str] = None
    action: str
    agent_id: Optional[str] = None
    details: Optional[str] = None
    created_at: str

    @classmethod
    def from_row(cls, row: dict) -> "AuditLogEntry":
        return cls(
            id=row["id"],
            project_id=row["project_id"],
            chat_id=row["chat_id"],
            action=row["action"],
            agent_id=row["agent_id"],
            details=row["details"],
            created_at=row["created_at"],
        )
