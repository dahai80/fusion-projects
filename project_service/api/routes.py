import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from project_service.engine.agent_binder import AgentBinder, AgentBinderError
from project_service.engine.chat_manager import ChatManager, ChatNotFound
from project_service.engine.instruction_engine import InstructionEngine
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
from project_service.engine.upstream_client import UpstreamClient
from project_service.models.agent_binding import (
    AgentBinding,
    AgentMeta,
    AgentPreview,
    PromptMergeMode,
)
from project_service.models.artifact_ref import ArtifactMigrateRequest, ArtifactRef
from project_service.models.chat import (
    Chat,
    ChatCreate,
    ChatForkRequest,
    ChatListItem,
    ChatSnapshot,
    ChatUpdate,
    Message,
    MessageCreate,
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


def get_upstream_client(request: Request) -> UpstreamClient:
    return request.app.state.upstream_client


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


@router.get("/agents", response_model=list[AgentMeta])
async def list_available_agents(
    ab: AgentBinder = Depends(get_agent_binder),
):
    return await ab.list_available_agents()


@router.get("/agents/{agent_id}", response_model=AgentPreview)
async def get_agent_preview(
    agent_id: str,
    ab: AgentBinder = Depends(get_agent_binder),
):
    preview = await ab.get_agent_preview(agent_id)
    if not preview:
        raise HTTPException(status_code=404, detail="agent not found or unavailable")
    return preview


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


# ── Upstream health ──


@router.get("/upstream/health")
async def upstream_health(
    uc: UpstreamClient = Depends(get_upstream_client),
):
    return await uc.health_check_all()


@router.get("/upstream/circuits")
async def upstream_circuits(
    uc: UpstreamClient = Depends(get_upstream_client),
):
    return uc.get_circuit_status()
