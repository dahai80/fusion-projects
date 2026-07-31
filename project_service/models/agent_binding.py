from enum import Enum
from typing import Optional

from pydantic import BaseModel


class PromptMergeMode(str, Enum):
    AGENT_FIRST = "AGENT_FIRST"
    PROJECT_ONLY = "PROJECT_ONLY"


class AgentBinding(BaseModel):
    project_id: str
    chat_id: Optional[str] = None
    agent_id: Optional[str] = None
    merge_mode: PromptMergeMode = PromptMergeMode.AGENT_FIRST

    @classmethod
    def from_project_row(cls, row: dict) -> "AgentBinding":
        return cls(
            project_id=row["id"],
            chat_id=None,
            agent_id=row.get("default_agent_id"),
            merge_mode=PromptMergeMode(row.get("prompt_merge_mode", "AGENT_FIRST")),
        )


class AgentPreview(BaseModel):
    agent_id: str
    name: str
    description: Optional[str] = None
    avatar: Optional[str] = None
    tools: Optional[list[str]] = None
    rag_enabled: bool = True
    permissions: Optional[list[str]] = None


class AgentMeta(BaseModel):
    agent_id: str
    name: str
    description: Optional[str] = None
    avatar: Optional[str] = None
