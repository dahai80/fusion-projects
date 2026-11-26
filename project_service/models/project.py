
from pydantic import BaseModel, Field

from project_service import config


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    default_agent_id: str | None = None
    prompt_merge_mode: str = config.DEFAULT_PROMPT_MERGE
    rag_mode: str = config.DEFAULT_RAG_MODE
    rag_top_k: int = Field(config.DEFAULT_RAG_TOP_K, ge=1, le=config.RAG_MAX_TOP_K)
    rag_threshold: float = Field(config.DEFAULT_RAG_THRESHOLD, ge=0.0, le=1.0)
    kb_id: str | None = None
    instructions: str | None = None
    template_id: str | None = None


class ProjectUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    default_agent_id: str | None = None
    prompt_merge_mode: str | None = None
    rag_mode: str | None = None
    rag_top_k: int | None = Field(None, ge=1, le=config.RAG_MAX_TOP_K)
    rag_threshold: float | None = Field(None, ge=0.0, le=1.0)
    kb_id: str | None = None


class Project(BaseModel):
    id: str
    name: str
    description: str
    is_archived: bool
    is_starred: bool
    default_agent_id: str | None = None
    prompt_merge_mode: str
    rag_mode: str
    rag_top_k: int
    rag_threshold: float
    kb_id: str | None = None
    tenant_id: str = ""
    artifact_count: int = 0
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
            tenant_id=row.get("tenant_id", ""),
            artifact_count=row.get("artifact_count", 0),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class ProjectListItem(BaseModel):
    id: str
    name: str
    description: str
    is_archived: bool
    is_starred: bool
    default_agent_id: str | None = None
    tenant_id: str = ""
    artifact_count: int = 0
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
            tenant_id=row.get("tenant_id", ""),
            artifact_count=row.get("artifact_count", 0),
            updated_at=row["updated_at"],
        )
