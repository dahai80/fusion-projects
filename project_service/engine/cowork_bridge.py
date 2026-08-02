import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from project_service.models.cowork import CoworkTask, CoworkTrigger
from project_service.store.project_store import ProjectStore

logger = logging.getLogger(__name__)


class CoworkBridge:
    def __init__(self, store: ProjectStore) -> None:
        self.store = store

    async def trigger_task(self, trigger: CoworkTrigger) -> CoworkTask:
        now = datetime.now(timezone.utc).isoformat()
        row = self.store.create_cowork_task({
            "id": uuid.uuid4().hex[:16],
            "project_id": trigger.project_id,
            "action": trigger.action,
            "payload": trigger.payload,
            "status": "pending",
            "result": None,
            "created_at": now,
            "updated_at": now,
        })
        logger.info("cowork task triggered id=%s action=%s project=%s",
                     row["id"], trigger.action, trigger.project_id)
        return CoworkTask.from_row(row)

    async def get_status(self, task_id: str) -> CoworkTask:
        row = self.store.get_cowork_task(task_id)
        if row is None:
            raise CoworkTaskNotFound(task_id)
        return CoworkTask.from_row(row)

    async def update_status(
        self, task_id: str, status: str, result: Optional[str] = None
    ) -> CoworkTask:
        now = datetime.now(timezone.utc).isoformat()
        row = self.store.update_cowork_task({
            "id": task_id,
            "status": status,
            "result": result,
            "updated_at": now,
        })
        logger.info("cowork task updated id=%s status=%s", task_id, status)
        return CoworkTask.from_row(row)


class CoworkTaskNotFound(Exception):
    pass
