
from pydantic import BaseModel


class AuditLogEntry(BaseModel):
    id: str
    project_id: str
    chat_id: str | None = None
    action: str
    agent_id: str | None = None
    details: str | None = None
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
