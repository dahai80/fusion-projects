import logging
from typing import Optional

from project_service.models.instruction import (
    InstructionContent,
    InstructionSave,
    InstructionSnapshot,
)
from project_service.engine.project_manager import ProjectManager
from project_service.store.project_store import ProjectStore

logger = logging.getLogger(__name__)


class SnapshotNotFound(Exception):
    pass


class InstructionEngine:
    def __init__(
        self,
        store: Optional[ProjectStore] = None,
        project_manager: Optional[ProjectManager] = None,
    ) -> None:
        self.store = store or ProjectStore()
        self.project_manager = project_manager or ProjectManager(store=self.store)

    async def get(self, project_id: str) -> InstructionContent:
        await self.project_manager.get(project_id)
        row = self.store.get_instructions(project_id)
        if not row:
            return InstructionContent(project_id=project_id, content="")
        return InstructionContent.from_row(row)

    async def save(self, project_id: str, payload: InstructionSave) -> InstructionContent:
        await self.project_manager.get(project_id)
        existing = self.store.get_instructions(project_id)
        if existing and existing["content"] and existing["content"] != payload.content:
            self.store.snapshot_instruction(project_id, existing["content"], label="auto")
        row = self.store.save_instructions(project_id, payload.content)
        logger.info("instructions saved project=%s len=%d", project_id, len(payload.content))
        return InstructionContent.from_row(row)

    async def clear(self, project_id: str) -> bool:
        await self.project_manager.get(project_id)
        return self.store.clear_instructions(project_id)

    async def list_snapshots(self, project_id: str) -> list[InstructionSnapshot]:
        await self.project_manager.get(project_id)
        rows = self.store.list_snapshots(project_id)
        return [InstructionSnapshot.from_row(r) for r in rows]

    async def restore_snapshot(self, snapshot_id: str) -> InstructionContent:
        snap = self.store.get_snapshot(snapshot_id)
        if not snap:
            raise SnapshotNotFound(snapshot_id)
        await self.project_manager.get(snap["project_id"])
        existing = self.store.get_instructions(snap["project_id"])
        if existing and existing["content"] and existing["content"] != snap["content"]:
            self.store.snapshot_instruction(snap["project_id"], existing["content"], label="pre-restore")
        row = self.store.save_instructions(snap["project_id"], snap["content"])
        logger.info("restored instruction snapshot=%s project=%s", snapshot_id, snap["project_id"])
        return InstructionContent.from_row(row)

    async def delete_snapshot(self, snapshot_id: str) -> bool:
        snap = self.store.get_snapshot(snapshot_id)
        if not snap:
            raise SnapshotNotFound(snapshot_id)
        deleted = self.store.delete_snapshot(snapshot_id)
        logger.info("deleted instruction snapshot=%s deleted=%s", snapshot_id, deleted)
        return deleted
