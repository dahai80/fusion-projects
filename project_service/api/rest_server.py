import logging
from contextlib import asynccontextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI

from project_service import config
from project_service.api.routes import router
from project_service.api.security import (
    AuthMiddleware,
    BodySizeMiddleware,
    RateLimitMiddleware,
)
from project_service.engine.agent_binder import AgentBinder
from project_service.engine.chat_manager import ChatManager
from project_service.engine.gateway_client import GatewayClient
from project_service.engine.instruction_engine import InstructionEngine
from project_service.engine.knowledge_manager import KnowledgeManager
from project_service.engine.project_manager import ProjectManager
from project_service.engine.rag_coordinator import RAGCoordinator
from project_service.mcp_server import MCPServer
from project_service.store.project_store import ProjectStore

logger = logging.getLogger(__name__)


def create_app(
    project_manager: Optional[ProjectManager] = None,
    instruction_engine: Optional[InstructionEngine] = None,
    chat_manager: Optional[ChatManager] = None,
    knowledge_manager: Optional[KnowledgeManager] = None,
    agent_binder: Optional[AgentBinder] = None,
    rag_coordinator: Optional[RAGCoordinator] = None,
) -> FastAPI:
    gateway_client = GatewayClient()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("rest lifespan startup auth=%s", "on" if config.REST_API_KEY else "off")
        yield
        logger.info("rest lifespan shutdown: closing gateway client")
        try:
            await gateway_client.close()
        except Exception as e:
            logger.error("gateway client close failed: %s", e)

    app = FastAPI(title="Fusion-Projects", version="0.3.2", lifespan=lifespan)
    store = getattr(project_manager, "store", None) if project_manager else None
    pm = project_manager or ProjectManager(upstream=gateway_client)
    pm_store = getattr(pm, "store", None) or store or ProjectStore()
    app.state.project_manager = pm
    app.state.instruction_engine = instruction_engine or InstructionEngine(
        store=pm_store, project_manager=pm
    )
    app.state.chat_manager = chat_manager or ChatManager(
        store=pm_store, project_manager=pm
    )
    rc = rag_coordinator or RAGCoordinator(
        store=pm_store, project_manager=pm, upstream=gateway_client
    )
    pm.rag_coordinator = rc
    app.state.rag_coordinator = rc
    app.state.knowledge_manager = knowledge_manager or KnowledgeManager(
        store=pm_store, project_manager=pm, rag_coordinator=rc
    )
    app.state.agent_binder = agent_binder or AgentBinder(
        store=pm_store, project_manager=pm, upstream=gateway_client
    )
    app.state.mcp_server = MCPServer()
    app.state.gateway_client = gateway_client
    app.add_middleware(AuthMiddleware)
    app.add_middleware(BodySizeMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.include_router(router)

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "fusion-project-svc", "auth": "on" if config.REST_API_KEY else "off"}

    logger.info("FastAPI app created")
    return app


app = create_app()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    config.ensure_dirs()
    logger.info("starting REST on %s:%s", config.REST_HOST, config.REST_PORT)
    uvicorn.run(app, host=config.REST_HOST, port=config.REST_PORT, log_level="info")


if __name__ == "__main__":
    main()
