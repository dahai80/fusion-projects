import logging
from typing import Optional

from project_service.engine.gateway_client import GatewayClient
from project_service.engine.project_manager import ProjectManager, ProjectNotFound
from project_service.models.agent_binding import (
    AgentBinding,
    AgentMeta,
    AgentPreview,
    PromptMergeMode,
)
from project_service.store.project_store import ProjectStore

logger = logging.getLogger(__name__)


class AgentBinderError(Exception):
    pass


class AgentUnavailable(AgentBinderError):
    pass


class AgentBinder:
    def __init__(
        self,
        store: Optional[ProjectStore] = None,
        project_manager: Optional[ProjectManager] = None,
        upstream: Optional[GatewayClient] = None,
    ) -> None:
        self.store = store or ProjectStore()
        self.project_manager = project_manager or ProjectManager()
        self.upstream = upstream or GatewayClient()

    async def _ensure_project(self, project_id: str) -> None:
        row = self.store.get_project(project_id)
        if not row:
            raise ProjectNotFound(project_id)

    async def get_binding(self, project_id: str, chat_id: Optional[str] = None) -> AgentBinding:
        await self._ensure_project(project_id)
        if chat_id:
            row = self.store.get_binding_by_chat(chat_id)
            if row:
                return AgentBinding(
                    project_id=row["project_id"],
                    chat_id=row["chat_id"],
                    agent_id=row["agent_id"],
                    merge_mode=PromptMergeMode(row["merge_mode"]),
                )
            row = self.store.get_binding_by_project(project_id)
        else:
            row = self.store.get_binding_by_project(project_id)
        if row:
            return AgentBinding(
                project_id=row["project_id"],
                chat_id=row["chat_id"],
                agent_id=row["agent_id"],
                merge_mode=PromptMergeMode(row["merge_mode"]),
            )
        project_row = self.store.get_project(project_id)
        return AgentBinding.from_project_row(project_row)

    async def set_binding(
        self,
        project_id: str,
        agent_id: Optional[str] = None,
        merge_mode: Optional[PromptMergeMode] = None,
        chat_id: Optional[str] = None,
    ) -> AgentBinding:
        await self._ensure_project(project_id)
        existing = self.store.get_binding_by_project(project_id) if not chat_id else self.store.get_binding_by_chat(chat_id)
        if existing:
            fields: dict = {}
            if agent_id is not None:
                fields["agent_id"] = agent_id
            if merge_mode is not None:
                fields["merge_mode"] = merge_mode.value
            if fields:
                self.store.update_binding(existing["id"], fields)
                logger.info("binding updated project=%s chat=%s fields=%s", project_id, chat_id, list(fields.keys()))
        else:
            data = {
                "project_id": project_id,
                "chat_id": chat_id,
                "agent_id": agent_id,
                "merge_mode": (merge_mode or PromptMergeMode.AGENT_FIRST).value,
            }
            self.store.create_binding(data)
            logger.info("binding created project=%s chat=%s agent=%s", project_id, chat_id, agent_id)
        return await self.get_binding(project_id, chat_id=chat_id)

    async def remove_binding(self, project_id: str, chat_id: Optional[str] = None) -> None:
        if chat_id:
            row = self.store.get_binding_by_chat(chat_id)
        else:
            row = self.store.get_binding_by_project(project_id)
        if row:
            self.store.delete_binding(row["id"])
            logger.info("binding removed project=%s chat=%s", project_id, chat_id)

    async def list_available_agents(self) -> list[AgentMeta]:
        result = await self.upstream.agent_list()
        if "error" in result:
            logger.warning("failed to list agents: %s", result.get("detail"))
            return []
        agents = result if isinstance(result, list) else result.get("agents", result.get("data", []))
        metas = []
        for a in agents:
            if isinstance(a, dict):
                metas.append(AgentMeta(
                    agent_id=a.get("id", a.get("agent_id", "")),
                    name=a.get("name", ""),
                    description=a.get("description"),
                    avatar=a.get("avatar"),
                ))
        return metas

    async def get_agent_preview(self, agent_id: str) -> Optional[AgentPreview]:
        result = await self.upstream.agent_get(agent_id)
        if "error" in result:
            logger.warning("failed to get agent %s: %s", agent_id, result.get("detail"))
            return None
        return AgentPreview(
            agent_id=result.get("id", result.get("agent_id", agent_id)),
            name=result.get("name", ""),
            description=result.get("description"),
            avatar=result.get("avatar"),
            tools=result.get("tools"),
            rag_enabled=result.get("rag_enabled", True),
            permissions=result.get("permissions"),
        )

    async def build_system_prompt(
        self,
        project_id: str,
        agent_prompt: Optional[str] = None,
        chat_id: Optional[str] = None,
    ) -> str:
        binding = await self.get_binding(project_id, chat_id=chat_id)
        instruction_row = self.store.get_instructions(project_id)
        project_instruction = instruction_row["content"] if instruction_row else ""
        parts: list[str] = []
        if binding.merge_mode == PromptMergeMode.AGENT_FIRST:
            if agent_prompt:
                parts.append(agent_prompt)
            if project_instruction:
                parts.append(project_instruction)
        elif binding.merge_mode == PromptMergeMode.PROJECT_ONLY:
            if project_instruction:
                parts.append(project_instruction)
        merged = "\n\n".join(parts)
        logger.info(
            "built system_prompt project=%s chat=%s mode=%s len=%d",
            project_id, chat_id, binding.merge_mode.value, len(merged),
        )
        return merged
