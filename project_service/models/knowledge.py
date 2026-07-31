from typing import Optional

from pydantic import BaseModel, Field


class FolderCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    parent_id: Optional[str] = None


class FolderUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    parent_id: Optional[str] = None


class KnowledgeFolder(BaseModel):
    id: str
    project_id: str
    name: str
    parent_id: Optional[str] = None
    sort_order: int = 0
    created_at: str

    @classmethod
    def from_row(cls, row: dict) -> "KnowledgeFolder":
        return cls(
            id=row["id"],
            project_id=row["project_id"],
            name=row["name"],
            parent_id=row["parent_id"],
            sort_order=row["sort_order"],
            created_at=row["created_at"],
        )


class KnowledgeFile(BaseModel):
    id: str
    project_id: str
    folder_id: Optional[str] = None
    name: str
    original_name: str
    file_path: str
    file_size: int = 0
    mime_type: Optional[str] = None
    rag_doc_id: Optional[str] = None
    index_status: str = "PENDING"
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: dict) -> "KnowledgeFile":
        return cls(
            id=row["id"],
            project_id=row["project_id"],
            folder_id=row["folder_id"],
            name=row["name"],
            original_name=row["original_name"],
            file_path=row["file_path"],
            file_size=row["file_size"],
            mime_type=row["mime_type"],
            rag_doc_id=row["rag_doc_id"],
            index_status=row["index_status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class FileIndexStatus(BaseModel):
    file_id: str
    name: str
    index_status: str
