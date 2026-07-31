from typing import Optional

from pydantic import BaseModel, Field

from project_service import config


class InstructionContent(BaseModel):
    project_id: str
    content: str = ""
    updated_at: Optional[str] = None

    @classmethod
    def from_row(cls, row: dict) -> "InstructionContent":
        return cls(
            project_id=row["project_id"],
            content=row["content"],
            updated_at=row["updated_at"],
        )


class InstructionSave(BaseModel):
    content: str = Field(..., max_length=config.MAX_INSTRUCTION_CHARS)


class InstructionSnapshot(BaseModel):
    id: str
    project_id: str
    content: str
    label: Optional[str] = None
    created_at: str

    @classmethod
    def from_row(cls, row: dict) -> "InstructionSnapshot":
        return cls(
            id=row["id"],
            project_id=row["project_id"],
            content=row["content"],
            label=row["label"],
            created_at=row["created_at"],
        )
