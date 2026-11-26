from enum import Enum

from pydantic import BaseModel


class PromptMergeMode(str, Enum):
    AGENT_FIRST = "AGENT_FIRST"
    PROJECT_ONLY = "PROJECT_ONLY"


class AgentBinding(BaseModel):
    project_id: str
    chat_id: str | None = None
    agent_id: str | None = None
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
    description: str | None = None
    avatar: str | None = None
    tools: list[str] | None = None
    rag_enabled: bool = True
    permissions: list[str] | None = None


class AgentMeta(BaseModel):
    agent_id: str
    name: str
    description: str | None = None
    avatar: str | None = None
