import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from project_service.engine.agent_binder import AgentBinder
from project_service.engine.chat_manager import ChatManager, ChatNotFound
from project_service.engine.instruction_engine import InstructionEngine, SnapshotNotFound
from project_service.engine.knowledge_manager import (
    FolderNotFound,
    KnowledgeFileNotFound,
    KnowledgeManager,
)
from project_service.engine.project_manager import (
    ArtifactAlreadyMigrated,
    ArtifactNotFound,
    ProjectNotArchived,
    ProjectNotFound,
    ProjectManager,
)
from project_service.engine.rag_coordinator import RAGCoordinator, RAGError
from project_service.models.agent_binding import (
    AgentBinding,
    PromptMergeMode,
)
from project_service.models.artifact_ref import ArtifactMigrateRequest, ArtifactRef
from project_service.models.audit import AuditLogEntry
from project_service.models.chat import (
    Chat,
    ChatCreate,
    ChatForkRequest,
    ChatListItem,
    ChatMoveRequest,
    ChatSnapshot,
    ChatUpdate,
    Message,
    MessageCreate,
    TempAttachment,
)
from project_service.models.instruction import (
    InstructionContent,
    InstructionSave,
    InstructionSnapshot,
)
from project_service.models.knowledge import (
    FileIndexStatus,
    FolderCreate,
    FolderUpdate,
    KnowledgeFile,
    KnowledgeFolder,
)
from project_service.mcp_server import MCPServer
from project_service.models.project import (
    Project,
    ProjectCreate,
    ProjectListItem,
    ProjectUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1")


def get_project_manager(request: Request) -> ProjectManager:
    return request.app.state.project_manager


def get_instruction_engine(request: Request) -> InstructionEngine:
    return request.app.state.instruction_engine


def get_chat_manager(request: Request) -> ChatManager:
    return request.app.state.chat_manager


def get_knowledge_manager(request: Request) -> KnowledgeManager:
    return request.app.state.knowledge_manager


def get_agent_binder(request: Request) -> AgentBinder:
    return request.app.state.agent_binder


def get_rag_coordinator(request: Request) -> RAGCoordinator:
    return request.app.state.rag_coordinator


# ── Project endpoints ──


@router.get("/projects", response_model=list[ProjectListItem])
async def list_projects(
    include_archived: bool = False,
    only_starred: bool = False,
    pm: ProjectManager = Depends(get_project_manager),
):
    return await pm.list(include_archived=include_archived, only_starred=only_starred)


@router.post("/projects", response_model=Project, status_code=201)
async def create_project(
    payload: ProjectCreate,
    pm: ProjectManager = Depends(get_project_manager),
):
    return await pm.create(payload)


@router.get("/projects/{project_id}", response_model=Project)
async def get_project(
    project_id: str,
    pm: ProjectManager = Depends(get_project_manager),
):
    try:
        return await pm.get(project_id)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail="project not found")


@router.patch("/projects/{project_id}", response_model=Project)
async def update_project(
    project_id: str,
    payload: ProjectUpdate,
    pm: ProjectManager = Depends(get_project_manager),
):
    try:
        return await pm.update(project_id, payload)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail="project not found")


@router.post("/projects/{project_id}/archive", response_model=Project)
async def archive_project(
    project_id: str,
    pm: ProjectManager = Depends(get_project_manager),
):
    try:
        return await pm.archive(project_id)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail="project not found")


@router.post("/projects/{project_id}/unarchive", response_model=Project)
async def unarchive_project(
    project_id: str,
    pm: ProjectManager = Depends(get_project_manager),
):
    try:
        return await pm.unarchive(project_id)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail="project not found")


@router.post("/projects/{project_id}/star", response_model=Project)
async def star_project(
    project_id: str,
    starred: bool = True,
    pm: ProjectManager = Depends(get_project_manager),
):
    try:
        return await pm.star(project_id, starred)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail="project not found")


@router.delete("/projects/{project_id}", status_code=204)
async def delete_project(
    project_id: str,
    pm: ProjectManager = Depends(get_project_manager),
):
    try:
        await pm.delete(project_id)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail="project not found")
    except ProjectNotArchived as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/projects/{project_id}/duplicate", response_model=Project, status_code=201)
async def duplicate_project(
    project_id: str,
    name: Optional[str] = None,
    pm: ProjectManager = Depends(get_project_manager),
):
    try:
        return await pm.duplicate_project(project_id, name=name)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail="project not found")


# ── Instruction endpoints ──


@router.get("/projects/{project_id}/instructions", response_model=InstructionContent)
async def get_instructions(
    project_id: str,
    ie: InstructionEngine = Depends(get_instruction_engine),
):
    try:
        return await ie.get(project_id)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail="project not found")


@router.put("/projects/{project_id}/instructions", response_model=InstructionContent)
async def save_instructions(
    project_id: str,
    payload: InstructionSave,
    ie: InstructionEngine = Depends(get_instruction_engine),
):
    try:
        return await ie.save(project_id, payload)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail="project not found")


@router.delete("/projects/{project_id}/instructions", status_code=204)
async def clear_instructions(
    project_id: str,
    ie: InstructionEngine = Depends(get_instruction_engine),
):
    try:
        await ie.clear(project_id)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail="project not found")


@router.get(
    "/projects/{project_id}/instructions/snapshots",
    response_model=list[InstructionSnapshot],
)
async def list_instruction_snapshots(
    project_id: str,
    ie: InstructionEngine = Depends(get_instruction_engine),
):
    try:
        return await ie.list_snapshots(project_id)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail="project not found")


@router.post(
    "/projects/{project_id}/instructions/snapshots/{snapshot_id}/restore",
    response_model=InstructionContent,
)
async def restore_instruction_snapshot(
    project_id: str,
    snapshot_id: str,
    ie: InstructionEngine = Depends(get_instruction_engine),
):
    try:
        return await ie.restore_snapshot(snapshot_id)
    except SnapshotNotFound:
        raise HTTPException(status_code=404, detail="snapshot not found")


@router.delete(
    "/projects/{project_id}/instructions/snapshots/{snapshot_id}",
    status_code=204,
)
async def delete_instruction_snapshot(
    project_id: str,
    snapshot_id: str,
    ie: InstructionEngine = Depends(get_instruction_engine),
):
    try:
        await ie.delete_snapshot(snapshot_id)
    except SnapshotNotFound:
        raise HTTPException(status_code=404, detail="snapshot not found")


# ── Artifact endpoints ──


@router.get("/projects/{project_id}/artifacts", response_model=list[ArtifactRef])
async def list_project_artifacts(
    project_id: str,
    type: Optional[str] = None,
    kind: Optional[str] = None,
    search: Optional[str] = None,
    pm: ProjectManager = Depends(get_project_manager),
):
    try:
        return await pm.list_artifacts(
            project_id,
            artifact_type=type,
            artifact_kind=kind,
            search=search,
        )
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail="project not found")


@router.post("/projects/{project_id}/artifacts", response_model=ArtifactRef, status_code=201)
async def migrate_artifact(
    project_id: str,
    payload: ArtifactMigrateRequest,
    pm: ProjectManager = Depends(get_project_manager),
):
    try:
        return await pm.migrate_artifact(project_id, payload.artifact_id)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail="project not found")
    except ArtifactAlreadyMigrated as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.delete("/projects/{project_id}/artifacts/{artifact_id}", status_code=204)
async def remove_project_artifact(
    project_id: str,
    artifact_id: str,
    pm: ProjectManager = Depends(get_project_manager),
):
    try:
        await pm.remove_artifact(artifact_id)
    except ArtifactNotFound:
        raise HTTPException(status_code=404, detail="artifact not found")


@router.post("/projects/{project_id}/artifacts/export")
async def export_project_artifacts(
    project_id: str,
    artifact_ids: Optional[list[str]] = None,
    pm: ProjectManager = Depends(get_project_manager),
):
    try:
        data = await pm.export_artifacts(project_id, artifact_ids=artifact_ids)
        return Response(
            content=data,
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename=project_{project_id}_artifacts.zip"},
        )
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail="project not found")
    except ArtifactNotFound:
        raise HTTPException(status_code=404, detail="no artifacts to export")


@router.post("/projects/{project_id}/export")
async def export_project(
    project_id: str,
    pm: ProjectManager = Depends(get_project_manager),
):
    try:
        data = await pm.export_project(project_id)
        return Response(
            content=data,
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename=project_{project_id}_full.zip"},
        )
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail="project not found")


# ── Chat endpoints ──


@router.get("/projects/{project_id}/chats", response_model=list[ChatListItem])
async def list_chats(
    project_id: str,
    only_starred: bool = False,
    cm: ChatManager = Depends(get_chat_manager),
):
    try:
        return await cm.list_chats(project_id, only_starred=only_starred)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail="project not found")


@router.post("/projects/{project_id}/chats", response_model=Chat, status_code=201)
async def create_chat(
    project_id: str,
    payload: ChatCreate,
    cm: ChatManager = Depends(get_chat_manager),
):
    try:
        return await cm.create_chat(project_id, payload)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail="project not found")


@router.get("/projects/{project_id}/chats/{chat_id}", response_model=Chat)
async def get_chat(
    project_id: str,
    chat_id: str,
    cm: ChatManager = Depends(get_chat_manager),
):
    try:
        return await cm.get_chat(chat_id)
    except ChatNotFound:
        raise HTTPException(status_code=404, detail="chat not found")


@router.patch("/projects/{project_id}/chats/{chat_id}", response_model=Chat)
async def update_chat(
    project_id: str,
    chat_id: str,
    payload: ChatUpdate,
    cm: ChatManager = Depends(get_chat_manager),
):
    try:
        fields = payload.model_dump(exclude_unset=True)
        return await cm.update_chat(chat_id, fields)
    except ChatNotFound:
        raise HTTPException(status_code=404, detail="chat not found")


@router.post("/projects/{project_id}/chats/{chat_id}/star", response_model=Chat)
async def star_chat(
    project_id: str,
    chat_id: str,
    starred: bool = True,
    cm: ChatManager = Depends(get_chat_manager),
):
    try:
        return await cm.star_chat(chat_id, starred)
    except ChatNotFound:
        raise HTTPException(status_code=404, detail="chat not found")


@router.delete("/projects/{project_id}/chats/{chat_id}", status_code=204)
async def delete_chat(
    project_id: str,
    chat_id: str,
    cm: ChatManager = Depends(get_chat_manager),
):
    try:
        await cm.delete_chat(chat_id)
    except ChatNotFound:
        raise HTTPException(status_code=404, detail="chat not found")


@router.post("/projects/{project_id}/chats/{chat_id}/fork", response_model=Chat, status_code=201)
async def fork_chat(
    project_id: str,
    chat_id: str,
    payload: Optional[ChatForkRequest] = None,
    cm: ChatManager = Depends(get_chat_manager),
):
    try:
        label = payload.label if payload else None
        return await cm.fork_chat(chat_id, label=label)
    except ChatNotFound:
        raise HTTPException(status_code=404, detail="chat not found")


@router.post("/projects/{project_id}/chats/{chat_id}/move", response_model=Chat)
async def move_chat(
    project_id: str,
    chat_id: str,
    payload: ChatMoveRequest,
    cm: ChatManager = Depends(get_chat_manager),
):
    try:
        return await cm.move_chat(chat_id, payload.target_project_id)
    except ChatNotFound:
        raise HTTPException(status_code=404, detail="chat not found")
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail="target project not found")


@router.post("/projects/{project_id}/chats/{chat_id}/detach", response_model=Chat)
async def detach_chat(
    project_id: str,
    chat_id: str,
    cm: ChatManager = Depends(get_chat_manager),
):
    try:
        return await cm.detach_chat(chat_id)
    except ChatNotFound:
        raise HTTPException(status_code=404, detail="chat not found")


# ── Chat snapshot endpoints ──


@router.post("/projects/{project_id}/chats/{chat_id}/snapshots", response_model=ChatSnapshot, status_code=201)
async def create_chat_snapshot(
    project_id: str,
    chat_id: str,
    cm: ChatManager = Depends(get_chat_manager),
):
    try:
        return await cm.create_snapshot(chat_id)
    except ChatNotFound:
        raise HTTPException(status_code=404, detail="chat not found")


@router.get("/projects/{project_id}/chats/{chat_id}/snapshots", response_model=list[ChatSnapshot])
async def list_chat_snapshots(
    project_id: str,
    chat_id: str,
    cm: ChatManager = Depends(get_chat_manager),
):
    try:
        return await cm.list_snapshots(chat_id)
    except ChatNotFound:
        raise HTTPException(status_code=404, detail="chat not found")


@router.post("/projects/{project_id}/chats/{chat_id}/snapshots/{snapshot_id}/restore", response_model=Chat)
async def restore_chat_snapshot(
    project_id: str,
    chat_id: str,
    snapshot_id: str,
    cm: ChatManager = Depends(get_chat_manager),
):
    try:
        return await cm.restore_snapshot(snapshot_id)
    except ChatNotFound:
        raise HTTPException(status_code=404, detail="snapshot not found")


@router.delete("/projects/{project_id}/chats/{chat_id}/snapshots/{snapshot_id}", status_code=204)
async def delete_chat_snapshot(
    project_id: str,
    chat_id: str,
    snapshot_id: str,
    cm: ChatManager = Depends(get_chat_manager),
):
    try:
        await cm.delete_snapshot(snapshot_id)
    except ChatNotFound:
        raise HTTPException(status_code=404, detail="snapshot not found")


# ── Message endpoints ──


@router.get("/projects/{project_id}/chats/{chat_id}/messages", response_model=list[Message])
async def list_messages(
    project_id: str,
    chat_id: str,
    limit: int = 100,
    offset: int = 0,
    cm: ChatManager = Depends(get_chat_manager),
):
    try:
        return await cm.list_messages(chat_id, limit=limit, offset=offset)
    except ChatNotFound:
        raise HTTPException(status_code=404, detail="chat not found")


@router.post("/projects/{project_id}/chats/{chat_id}/messages", response_model=Message, status_code=201)
async def add_message(
    project_id: str,
    chat_id: str,
    payload: MessageCreate,
    cm: ChatManager = Depends(get_chat_manager),
):
    try:
        return await cm.add_message(chat_id, payload)
    except ChatNotFound:
        raise HTTPException(status_code=404, detail="chat not found")


@router.delete("/projects/{project_id}/chats/{chat_id}/messages/{message_id}", status_code=204)
async def delete_message(
    project_id: str,
    chat_id: str,
    message_id: str,
    cm: ChatManager = Depends(get_chat_manager),
):
    try:
        await cm.delete_message(message_id)
    except ChatNotFound:
        raise HTTPException(status_code=404, detail="message not found")


@router.post("/projects/{project_id}/chats/{chat_id}/messages/stream")
async def stream_message(
    project_id: str,
    chat_id: str,
    payload: MessageCreate,
    cm: ChatManager = Depends(get_chat_manager),
):
    try:
        msg = await cm.add_message(chat_id, payload)

        async def event_stream():
            yield f"data: {json.dumps({'type': 'message', 'message': msg.model_dump()})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")
    except ChatNotFound:
        raise HTTPException(status_code=404, detail="chat not found")


# ── Temp attachment endpoints ──


@router.post(
    "/projects/{project_id}/chats/{chat_id}/temp-attachments",
    response_model=TempAttachment,
    status_code=201,
)
async def add_temp_attachment(
    project_id: str,
    chat_id: str,
    file_path: str,
    original_name: str,
    file_size: int = 0,
    mime_type: Optional[str] = None,
    cm: ChatManager = Depends(get_chat_manager),
):
    try:
        return await cm.add_temp_attachment(
            chat_id, file_path, original_name,
            file_size=file_size, mime_type=mime_type,
        )
    except ChatNotFound:
        raise HTTPException(status_code=404, detail="chat not found")


@router.get(
    "/projects/{project_id}/chats/{chat_id}/temp-attachments",
    response_model=list[TempAttachment],
)
async def list_temp_attachments(
    project_id: str,
    chat_id: str,
    cm: ChatManager = Depends(get_chat_manager),
):
    try:
        return await cm.list_temp_attachments(chat_id)
    except ChatNotFound:
        raise HTTPException(status_code=404, detail="chat not found")


@router.delete(
    "/projects/{project_id}/chats/{chat_id}/temp-attachments/{attachment_id}",
    status_code=204,
)
async def delete_temp_attachment(
    project_id: str,
    chat_id: str,
    attachment_id: str,
    cm: ChatManager = Depends(get_chat_manager),
):
    try:
        await cm.delete_temp_attachment(attachment_id)
    except ChatNotFound:
        raise HTTPException(status_code=404, detail="temp attachment not found")


# ── Knowledge folder endpoints ──


@router.get("/projects/{project_id}/knowledge/folders", response_model=list[KnowledgeFolder])
async def list_folders(
    project_id: str,
    parent_id: Optional[str] = None,
    km: KnowledgeManager = Depends(get_knowledge_manager),
):
    try:
        return await km.list_folders(project_id, parent_id=parent_id)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail="project not found")


@router.post("/projects/{project_id}/knowledge/folders", response_model=KnowledgeFolder, status_code=201)
async def create_folder(
    project_id: str,
    payload: FolderCreate,
    km: KnowledgeManager = Depends(get_knowledge_manager),
):
    try:
        return await km.create_folder(project_id, payload)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail="project not found")


@router.patch("/projects/{project_id}/knowledge/folders/{folder_id}", response_model=KnowledgeFolder)
async def update_folder(
    project_id: str,
    folder_id: str,
    payload: FolderUpdate,
    km: KnowledgeManager = Depends(get_knowledge_manager),
):
    try:
        return await km.update_folder(folder_id, payload)
    except FolderNotFound:
        raise HTTPException(status_code=404, detail="folder not found")


@router.delete("/projects/{project_id}/knowledge/folders/{folder_id}", status_code=204)
async def delete_folder(
    project_id: str,
    folder_id: str,
    km: KnowledgeManager = Depends(get_knowledge_manager),
):
    try:
        await km.delete_folder(folder_id)
    except FolderNotFound:
        raise HTTPException(status_code=404, detail="folder not found")


# ── Knowledge file endpoints ──


@router.get("/projects/{project_id}/knowledge/files", response_model=list[KnowledgeFile])
async def list_knowledge_files(
    project_id: str,
    folder_id: Optional[str] = None,
    km: KnowledgeManager = Depends(get_knowledge_manager),
):
    try:
        return await km.list_files(project_id, folder_id=folder_id)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail="project not found")


@router.get("/projects/{project_id}/knowledge/files/{file_id}", response_model=KnowledgeFile)
async def get_knowledge_file(
    project_id: str,
    file_id: str,
    km: KnowledgeManager = Depends(get_knowledge_manager),
):
    try:
        return await km.get_file(file_id)
    except KnowledgeFileNotFound:
        raise HTTPException(status_code=404, detail="file not found")


@router.delete("/projects/{project_id}/knowledge/files/{file_id}", status_code=204)
async def delete_knowledge_file(
    project_id: str,
    file_id: str,
    km: KnowledgeManager = Depends(get_knowledge_manager),
):
    try:
        await km.delete_file(file_id)
    except KnowledgeFileNotFound:
        raise HTTPException(status_code=404, detail="file not found")


@router.post("/projects/{project_id}/knowledge/files/upload", response_model=KnowledgeFile, status_code=201)
async def upload_knowledge_file(
    project_id: str,
    source_path: str,
    original_name: str,
    folder_id: Optional[str] = None,
    mime_type: Optional[str] = None,
    km: KnowledgeManager = Depends(get_knowledge_manager),
):
    try:
        return await km.upload_file(
            project_id, source_path, original_name,
            folder_id=folder_id, mime_type=mime_type,
        )
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail="project not found")


@router.post("/projects/{project_id}/knowledge/files/{file_id}/replace", response_model=KnowledgeFile)
async def replace_knowledge_file(
    project_id: str,
    file_id: str,
    source_path: str,
    km: KnowledgeManager = Depends(get_knowledge_manager),
):
    try:
        return await km.replace_file(file_id, source_path)
    except KnowledgeFileNotFound:
        raise HTTPException(status_code=404, detail="file not found")


@router.patch("/projects/{project_id}/knowledge/files/{file_id}", response_model=KnowledgeFile)
async def update_knowledge_file(
    project_id: str,
    file_id: str,
    name: Optional[str] = None,
    folder_id: Optional[str] = None,
    km: KnowledgeManager = Depends(get_knowledge_manager),
):
    try:
        if name is not None:
            return await km.rename_file(file_id, name)
        if folder_id is not None:
            return await km.move_file(file_id, folder_id)
        raise HTTPException(status_code=400, detail="name or folder_id required")
    except KnowledgeFileNotFound:
        raise HTTPException(status_code=404, detail="file not found")


@router.get("/projects/{project_id}/knowledge/status", response_model=list[FileIndexStatus])
async def list_file_statuses(
    project_id: str,
    km: KnowledgeManager = Depends(get_knowledge_manager),
):
    try:
        return await km.list_file_statuses(project_id)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail="project not found")


# ── Agent binding endpoints ──


@router.get("/projects/{project_id}/agent", response_model=AgentBinding)
async def get_agent_binding(
    project_id: str,
    chat_id: Optional[str] = None,
    ab: AgentBinder = Depends(get_agent_binder),
):
    try:
        return await ab.get_binding(project_id, chat_id=chat_id)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail="project not found")


@router.put("/projects/{project_id}/agent", response_model=AgentBinding)
async def set_agent_binding(
    project_id: str,
    agent_id: Optional[str] = None,
    merge_mode: Optional[PromptMergeMode] = None,
    chat_id: Optional[str] = None,
    ab: AgentBinder = Depends(get_agent_binder),
):
    try:
        return await ab.set_binding(project_id, agent_id=agent_id, merge_mode=merge_mode, chat_id=chat_id)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail="project not found")


@router.delete("/projects/{project_id}/agent", status_code=204)
async def remove_agent_binding(
    project_id: str,
    chat_id: Optional[str] = None,
    ab: AgentBinder = Depends(get_agent_binder),
):
    await ab.remove_binding(project_id, chat_id=chat_id)


@router.post("/projects/{project_id}/system-prompt")
async def build_system_prompt(
    project_id: str,
    agent_prompt: Optional[str] = None,
    chat_id: Optional[str] = None,
    ab: AgentBinder = Depends(get_agent_binder),
):
    try:
        prompt = await ab.build_system_prompt(project_id, agent_prompt=agent_prompt, chat_id=chat_id)
        return {"system_prompt": prompt}
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail="project not found")


# ── RAG endpoints ──


@router.post("/projects/{project_id}/rag/index/{file_id}")
async def index_file(
    project_id: str,
    file_id: str,
    rc: RAGCoordinator = Depends(get_rag_coordinator),
):
    try:
        return await rc.index_file(file_id)
    except RAGError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/projects/{project_id}/rag/index-folder/{folder_id}")
async def index_folder(
    project_id: str,
    folder_id: str,
    rc: RAGCoordinator = Depends(get_rag_coordinator),
):
    try:
        results = await rc.index_folder(folder_id)
        return {"indexed": len(results), "results": results}
    except RAGError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/projects/{project_id}/rag/query")
async def rag_query(
    project_id: str,
    query: str,
    mode: Optional[str] = None,
    folder_ids: Optional[list[str]] = None,
    top_k: Optional[int] = None,
    threshold: Optional[float] = None,
    chat_id: Optional[str] = None,
    rc: RAGCoordinator = Depends(get_rag_coordinator),
):
    try:
        return await rc.query(
            project_id, query,
            mode=mode, folder_ids=folder_ids, top_k=top_k,
            threshold=threshold, chat_id=chat_id,
        )
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail="project not found")


@router.delete("/projects/{project_id}/rag/index/{file_id}")
async def remove_file_index(
    project_id: str,
    file_id: str,
    rc: RAGCoordinator = Depends(get_rag_coordinator),
):
    try:
        return await rc.remove_file_index(file_id)
    except RAGError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/projects/{project_id}/rag/status")
async def rag_status(
    project_id: str,
    rc: RAGCoordinator = Depends(get_rag_coordinator),
):
    try:
        return await rc.get_rag_status(project_id)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail="project not found")


@router.get("/projects/{project_id}/rag/config")
async def get_rag_config(
    project_id: str,
    pm: ProjectManager = Depends(get_project_manager),
):
    try:
        proj = await pm.get(project_id)
        return {
            "rag_mode": proj.rag_mode,
            "rag_top_k": proj.rag_top_k,
            "rag_threshold": proj.rag_threshold,
        }
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail="project not found")


@router.put("/projects/{project_id}/rag/config")
async def set_rag_config(
    project_id: str,
    rag_mode: Optional[str] = None,
    rag_top_k: Optional[int] = None,
    rag_threshold: Optional[float] = None,
    pm: ProjectManager = Depends(get_project_manager),
):
    try:
        fields = {}
        if rag_mode is not None:
            fields["rag_mode"] = rag_mode
        if rag_top_k is not None:
            fields["rag_top_k"] = rag_top_k
        if rag_threshold is not None:
            fields["rag_threshold"] = rag_threshold
        payload = ProjectUpdate(**fields)
        proj = await pm.update(project_id, payload)
        return {
            "rag_mode": proj.rag_mode,
            "rag_top_k": proj.rag_top_k,
            "rag_threshold": proj.rag_threshold,
        }
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail="project not found")


# ── Audit log endpoints ──


@router.get("/projects/{project_id}/audit", response_model=list[AuditLogEntry])
async def list_audit_log(
    project_id: str,
    limit: int = 100,
    offset: int = 0,
    pm: ProjectManager = Depends(get_project_manager),
):
    try:
        await pm.get(project_id)
        rows = pm.store.list_audit_log(project_id, limit=limit, offset=offset)
        return [AuditLogEntry.from_row(r) for r in rows]
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail="project not found")


@router.post("/projects/{project_id}/audit", response_model=AuditLogEntry, status_code=201)
async def create_audit_log(
    project_id: str,
    action: str,
    chat_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    details: Optional[str] = None,
    pm: ProjectManager = Depends(get_project_manager),
):
    try:
        await pm.get(project_id)
        row = pm.store.create_audit_log({
            "project_id": project_id,
            "chat_id": chat_id,
            "action": action,
            "agent_id": agent_id,
            "details": details,
        })
        return AuditLogEntry.from_row(row)
    except ProjectNotFound:
        raise HTTPException(status_code=404, detail="project not found")


# ── MCP endpoint ──


def get_mcp_server(request: Request) -> MCPServer:
    return request.app.state.mcp_server


@router.post("/mcp")
async def mcp_endpoint(
    request: Request,
    mcp: MCPServer = Depends(get_mcp_server),
):
    body = await request.body()
    resp = await mcp.handle_request(body)
    if not resp:
        return Response(status_code=204)
    return Response(content=resp, media_type="application/json")
