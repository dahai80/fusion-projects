import io
import json
import logging
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from project_service.engine.gateway_client import GatewayClient
from project_service.models.artifact_ref import ArtifactRef
from project_service.models.project import (
    Project,
    ProjectCreate,
    ProjectListItem,
    ProjectUpdate,
)
from project_service.store.file_store import FileStore
from project_service.store.project_store import ProjectStore

if TYPE_CHECKING:
    from project_service.engine.rag_coordinator import RAGCoordinator

logger = logging.getLogger(__name__)

_TYPE_EXTENSIONS = {
    "html": ".html",
    "react": ".jsx",
    "code": ".py",
    "markdown": ".md",
    "data": ".json",
}


def _type_extension(artifact_type: str) -> str:
    return _TYPE_EXTENSIONS.get(artifact_type, ".txt")


class ProjectError(Exception):
    pass


class ProjectNotFound(ProjectError):
    pass


class ProjectNotArchived(ProjectError):
    pass


class ArtifactAlreadyMigrated(ProjectError):
    pass


class ArtifactNotFound(ProjectError):
    pass


class ProjectManager:
    def __init__(
        self,
        store: Optional[ProjectStore] = None,
        file_store: Optional[FileStore] = None,
        upstream: Optional[GatewayClient] = None,
        rag_coordinator: Optional["RAGCoordinator"] = None,
    ) -> None:
        self.store = store or ProjectStore()
        self.file_store = file_store or FileStore()
        self.upstream = upstream or GatewayClient()
        self.rag_coordinator = rag_coordinator

    async def create(self, payload: ProjectCreate) -> Project:
        data = payload.model_dump()
        instructions_text = data.pop("instructions", None)
        template_id = data.pop("template_id", None)
        row = self.store.create_project(data)
        self.file_store.init_project(row["id"])
        if instructions_text:
            self.store.save_instructions(row["id"], instructions_text)
            logger.info("project created with instructions id=%s len=%d", row["id"], len(instructions_text))
        else:
            logger.info("project created id=%s name=%s", row["id"], row["name"])
        if template_id:
            try:
                source = await self.get(template_id)
                await self._copy_project_content(source, row["id"], copy_instructions=True, copy_chats=False)
                logger.info("project templated from=%s to=%s", template_id, row["id"])
            except Exception as e:
                logger.warning("template copy failed from=%s to=%s err=%s (project kept)", template_id, row["id"], e)
        return Project.from_row(self.store.get_project(row["id"]))

    async def get(self, project_id: str) -> Project:
        row = self.store.get_project(project_id)
        if not row:
            raise ProjectNotFound(project_id)
        return Project.from_row(row)

    async def list(
        self,
        include_archived: bool = False,
        only_starred: bool = False,
    ) -> list[ProjectListItem]:
        rows = self.store.list_projects(
            include_archived=include_archived,
            only_starred=only_starred,
        )
        return [ProjectListItem.from_row(r) for r in rows]

    async def update(self, project_id: str, payload: ProjectUpdate) -> Project:
        fields = payload.model_dump(exclude_unset=True)
        row = self.store.update_project(project_id, fields)
        if not row:
            raise ProjectNotFound(project_id)
        logger.info("project updated id=%s fields=%s", project_id, list(fields.keys()))
        return Project.from_row(row)

    async def archive(self, project_id: str) -> Project:
        row = self.store.set_archived(project_id, True)
        if not row:
            raise ProjectNotFound(project_id)
        return Project.from_row(row)

    async def unarchive(self, project_id: str) -> Project:
        row = self.store.set_archived(project_id, False)
        if not row:
            raise ProjectNotFound(project_id)
        return Project.from_row(row)

    async def star(self, project_id: str, starred: bool = True) -> Project:
        row = self.store.set_starred(project_id, starred)
        if not row:
            raise ProjectNotFound(project_id)
        return Project.from_row(row)

    async def delete(self, project_id: str) -> None:
        row = self.store.get_project(project_id)
        if not row:
            raise ProjectNotFound(project_id)
        if not row["is_archived"]:
            raise ProjectNotArchived(
                "project must be archived before delete: " + project_id
            )
        artifact_rows = self.store.list_artifact_refs(project_id)
        for ar in artifact_rows:
            try:
                await self._call_artifacts_engine(
                    "artifact.update",
                    {"artifact_id": ar["artifact_id"], "in_project_kb": False},
                )
            except Exception:
                logger.warning("failed to clear in_project_kb for artifact=%s", ar["artifact_id"])
        self.store.delete_project(project_id)
        self.file_store.remove_project(project_id)
        logger.info("project deleted id=%s artifacts_cleared=%d", project_id, len(artifact_rows))

    async def _call_artifacts_engine(self, method: str, params: dict) -> dict:
        result = await self.upstream.artifacts_call(method, params)
        if not isinstance(result, dict) or "error" in result:
            raise ProjectError(f"artifacts-engine error: {result}")
        return result

    async def migrate_artifact(
        self,
        project_id: str,
        artifact_id: str,
    ) -> ArtifactRef:
        await self.get(project_id)
        existing = self.store.get_artifact_ref(artifact_id)
        if existing:
            raise ArtifactAlreadyMigrated(
                f"artifact {artifact_id} already migrated to project {existing['project_id']}"
            )
        result = await self._call_artifacts_engine(
            "artifact.get", {"artifact_id": artifact_id}
        )
        artifact = result.get("artifact", {})
        await self._call_artifacts_engine(
            "artifact.move_to_project_kb", {"artifact_id": artifact_id}
        )
        ref_data = {
            "project_id": project_id,
            "artifact_id": artifact_id,
            "artifact_name": artifact.get("name", ""),
            "artifact_type": artifact.get("type", ""),
            "artifact_kind": artifact.get("kind"),
            "content_summary": artifact.get("summary"),
            "source_session_id": artifact.get("session_id"),
        }
        row = self.store.create_artifact_ref(ref_data)
        logger.info("artifact migrated artifact=%s project=%s", artifact_id, project_id)
        return ArtifactRef.from_row(row)

    async def list_artifacts(
        self,
        project_id: str,
        artifact_type: Optional[str] = None,
        artifact_kind: Optional[str] = None,
        search: Optional[str] = None,
    ) -> list[ArtifactRef]:
        await self.get(project_id)
        rows = self.store.list_artifact_refs(
            project_id,
            artifact_type=artifact_type,
            artifact_kind=artifact_kind,
            search=search,
        )
        return [ArtifactRef.from_row(r) for r in rows]

    async def export_artifacts(
        self,
        project_id: str,
        artifact_ids: Optional[list[str]] = None,
    ) -> bytes:
        await self.get(project_id)
        if artifact_ids:
            refs = []
            for aid in artifact_ids:
                ref = self.store.get_artifact_ref(aid)
                if ref and ref["project_id"] == project_id:
                    refs.append(ref)
        else:
            refs = self.store.list_artifact_refs(project_id)
        if not refs:
            raise ArtifactNotFound("no artifacts to export")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for ref in refs:
                try:
                    result = await self._call_artifacts_engine(
                        "artifact.get", {"artifact_id": ref["artifact_id"]}
                    )
                    content = result.get("artifact", {}).get("content", "")
                    filename = f"{ref['artifact_name'] or ref['artifact_id']}"
                    ext = _type_extension(ref.get("artifact_type", ""))
                    zf.writestr(f"{filename}{ext}", content)
                except Exception:
                    logger.warning("export failed for artifact=%s, skipping", ref["artifact_id"])
        logger.info("exported artifacts project=%s count=%d", project_id, len(refs))
        return buf.getvalue()

    async def export_project(self, project_id: str) -> bytes:
        proj = await self.get(project_id)
        await self.get(project_id)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("project.json", json.dumps(proj.model_dump(), ensure_ascii=False, indent=2))
            instr = self.store.get_instructions(project_id)
            if instr:
                zf.writestr("instructions.json", json.dumps(instr, ensure_ascii=False, indent=2))
            chats = self.store.list_chats(project_id)
            if chats:
                zf.writestr("chats.json", json.dumps(chats, ensure_ascii=False, indent=2))
                for c in chats:
                    cid = c["id"]
                    msgs = self.store.list_messages(cid)
                    if msgs:
                        zf.writestr(f"chats/{cid}/messages.json", json.dumps(msgs, ensure_ascii=False, indent=2))
            folders = self.store.list_folders(project_id)
            if folders:
                zf.writestr("knowledge_folders.json", json.dumps(folders, ensure_ascii=False, indent=2))
            files = self.store.list_knowledge_files(project_id)
            if files:
                zf.writestr("knowledge_files.json", json.dumps(files, ensure_ascii=False, indent=2))
            binding = self.store.get_binding_by_project(project_id)
            if binding:
                zf.writestr("agent_binding.json", json.dumps(binding, ensure_ascii=False, indent=2))
        logger.info("exported full project=%s", project_id)
        return buf.getvalue()

    async def remove_artifact(self, artifact_id: str) -> bool:
        existing = self.store.get_artifact_ref(artifact_id)
        if not existing:
            raise ArtifactNotFound(artifact_id)
        removed = self.store.remove_artifact_ref(artifact_id)
        if removed:
            logger.info("artifact ref removed artifact=%s", artifact_id)
        return removed

    async def duplicate_project(
        self,
        project_id: str,
        name: Optional[str] = None,
        copy_chats: bool = False,
    ) -> Project:
        source = await self.get(project_id)
        new_name = name or f"{source.name} (copy)"
        payload = ProjectCreate(
            name=new_name,
            description=source.description,
            default_agent_id=source.default_agent_id,
            prompt_merge_mode=source.prompt_merge_mode,
            rag_mode=source.rag_mode,
            rag_top_k=source.rag_top_k,
            rag_threshold=source.rag_threshold,
        )
        new_proj = await self.create(payload)
        await self._copy_project_content(
            source, new_proj.id, copy_instructions=True, copy_chats=copy_chats
        )
        logger.info("project duplicated from=%s to=%s chats=%s", project_id, new_proj.id, copy_chats)
        return new_proj

    async def _copy_project_content(
        self,
        source: Project,
        dest_id: str,
        copy_instructions: bool = True,
        copy_chats: bool = False,
    ) -> None:
        source_id = source.id
        if copy_instructions:
            instr = self.store.get_instructions(source_id)
            if instr and instr.get("content"):
                self.store.save_instructions(dest_id, instr["content"])
        bindings = self.store.get_binding_by_project(source_id)
        if bindings:
            self.store.create_binding({
                "project_id": dest_id,
                "agent_id": bindings["agent_id"],
                "merge_mode": bindings["merge_mode"],
            })
        folders = self.store.list_folders(source_id)
        folder_id_map: dict[Optional[str], Optional[str]] = {}
        for f in folders:
            new_folder = self.store.create_folder({
                "project_id": dest_id,
                "name": f["name"],
                "parent_id": folder_id_map.get(f["parent_id"]),
                "sort_order": f.get("sort_order", 0),
            })
            folder_id_map[f["id"]] = new_folder["id"]
        files = self.store.list_knowledge_files(source_id)
        for kfile in files:
            old_path = Path(kfile["file_path"])
            folder_id = folder_id_map.get(kfile["folder_id"])
            dest_dir = self.file_store.project_dir(dest_id) / "knowledge"
            if folder_id:
                dest_dir = dest_dir / folder_id
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_name = kfile["original_name"] or old_path.name
            dest_path = dest_dir / dest_name
            if dest_path.exists():
                dest_path = dest_dir / f"{dest_path.stem}_{uuid.uuid4().hex[:8]}{dest_path.suffix}"
            if old_path.exists():
                shutil.copy2(str(old_path), str(dest_path))
            new_file = self.store.create_knowledge_file({
                "project_id": dest_id,
                "folder_id": folder_id,
                "name": dest_path.stem,
                "original_name": dest_name,
                "file_path": str(dest_path),
                "file_size": kfile.get("file_size", 0),
                "mime_type": kfile.get("mime_type"),
                "index_status": "PENDING",
            })
            if self.rag_coordinator is not None and old_path.exists():
                try:
                    await self.rag_coordinator.index_file(new_file["id"])
                except Exception as e:
                    logger.warning("re-index failed file=%s dest=%s err=%s (left PENDING)", new_file["id"], dest_id, e)
        if copy_chats:
            chats = self.store.list_chats(source_id)
            for c in chats:
                new_chat = self.store.create_chat({
                    "project_id": dest_id,
                    "title": c["title"],
                    "agent_id": c.get("agent_id"),
                })
                msgs = self.store.list_messages(c["id"])
                for m in msgs:
                    self.store.create_message({
                        "chat_id": new_chat["id"],
                        "role": m["role"],
                        "content": m["content"],
                        "rag_sources": m.get("rag_sources"),
                        "tool_calls": m.get("tool_calls"),
                        "token_usage": m.get("token_usage"),
                    })
        logger.info(
            "content copied from=%s to=%s folders=%d files=%d chats=%s",
            source_id, dest_id, len(folders), len(files), copy_chats,
        )
