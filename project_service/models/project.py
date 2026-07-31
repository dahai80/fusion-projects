from typing import Optional

from pydantic import BaseModel, Field

from project_service import config


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    default_agent_id: Optional[str] = None
    prompt_merge_mode: str = config.DEFAULT_PROMPT_MERGE
    rag_mode: str = config.DEFAULT_RAG_MODE
    rag_top_k: int = config.DEFAULT_RAG_TOP_K
    rag_threshold: float = config.DEFAULT_RAG_THRESHOLD
    kb_id: Optional[str] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    default_agent_id: Optional[str] = None
    prompt_merge_mode: Optional[str] = None
    rag_mode: Optional[str] = None
    rag_top_k: Optional[int] = None
    rag_threshold: Optional[float] = None
    kb_id: Optional[str] = None


class Project(BaseModel):
    id: str
    name: str
    description: str
    is_archived: bool
    is_starred: bool
    default_agent_id: Optional[str] = None
    prompt_merge_mode: str
    rag_mode: str
    rag_top_k: int
    rag_threshold: float
    kb_id: Optional[str] = None
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: dict) -> "Project":
        return cls(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            is_archived=bool(row["is_archived"]),
            is_starred=bool(row["is_starred"]),
            default_agent_id=row["default_agent_id"],
            prompt_merge_mode=row["prompt_merge_mode"],
            rag_mode=row["rag_mode"],
            rag_top_k=row["rag_top_k"],
            rag_threshold=row["rag_threshold"],
            kb_id=row["kb_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class ProjectListItem(BaseModel):
    id: str
    name: str
    description: str
    is_archived: bool
    is_starred: bool
    default_agent_id: Optional[str] = None
    updated_at: str

    @classmethod
    def from_row(cls, row: dict) -> "ProjectListItem":
        return cls(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            is_archived=bool(row["is_archived"]),
            is_starred=bool(row["is_starred"]),
            default_agent_id=row["default_agent_id"],
            updated_at=row["updated_at"],
        )
