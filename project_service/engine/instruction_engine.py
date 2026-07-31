import logging
from typing import Optional

from project_service.models.instruction import (
    InstructionContent,
    InstructionSave,
    InstructionSnapshot,
)
from project_service.engine.project_manager import ProjectManager, ProjectNotFound
from project_service.store.project_store import ProjectStore

logger = logging.getLogger(__name__)


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
