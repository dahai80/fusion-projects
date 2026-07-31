import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel


class CoworkTask(BaseModel):
    id: str
    project_id: str
    action: str
    payload: Optional[str] = None
    status: str = "pending"
    result: Optional[str] = None
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: dict) -> "CoworkTask":
        return cls(
            id=row["id"],
            project_id=row["project_id"],
            action=row["action"],
            payload=row["payload"],
            status=row["status"],
            result=row["result"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class CoworkTrigger(BaseModel):
    project_id: str
    action: str
    payload: Optional[str] = None
