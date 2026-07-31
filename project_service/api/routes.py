import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from project_service.engine.instruction_engine import InstructionEngine
from project_service.engine.project_manager import (
    ProjectNotArchived,
    ProjectNotFound,
    ProjectManager,
)
from project_service.models.instruction import (
    InstructionContent,
    InstructionSave,
    InstructionSnapshot,
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
