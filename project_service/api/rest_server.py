import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from project_service import config
from project_service.api.routes import router
from project_service.daemon_server import ProjectRPCServer
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
        auth_on = bool(config.REST_API_KEY)
        logger.info("rest lifespan startup auth=%s host=%s", "on" if auth_on else "off", config.REST_HOST)
        if not auth_on and not config.rest_host_is_loopback() and not config.REST_ALLOW_NO_AUTH:
            msg = (
                "REFUSING to start: REST auth disabled (no FUSION_REST_API_KEY) and "
                "bound to non-loopback host %s. Set FUSION_REST_API_KEY, or bind to "
                "127.0.0.1, or set FUSION_REST_ALLOW_NO_AUTH=1 to acknowledge."
            ) % config.REST_HOST
            logger.critical(msg)
            raise RuntimeError(msg)
        yield
        logger.info("rest lifespan shutdown: closing gateway client")
        try:
            await gateway_client.close()
        except Exception as e:
            logger.error("gateway client close failed: %s", e)
        logger.info("rest lifespan shutdown: closing project store")
        try:
            if pm_store is not None and hasattr(pm_store, "close"):
                pm_store.close()
        except Exception as e:
            logger.error("project store close failed: %s", e)

    app = FastAPI(title="Fusion-Projects", version="0.4.4", lifespan=lifespan)
    injected_store = getattr(project_manager, "store", None) if project_manager else None
    if project_manager is not None:
        pm = project_manager
    else:
        pm = ProjectManager(upstream=gateway_client)
    pm_store = injected_store or getattr(pm, "store", None)
    if pm_store is None:
        pm_store = ProjectStore()
    app.state.project_manager = pm
    app.state.instruction_engine = instruction_engine or InstructionEngine(
        store=pm_store, project_manager=pm
    )
    app.state.chat_manager = chat_manager or ChatManager(
        store=pm_store, project_manager=pm,
        file_store=getattr(pm, "file_store", None),
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
    shared_rpc = ProjectRPCServer(
        project_manager=pm,
        instruction_engine=app.state.instruction_engine,
        chat_manager=app.state.chat_manager,
        knowledge_manager=app.state.knowledge_manager,
        agent_binder=app.state.agent_binder,
        rag_coordinator=rc,
        upstream=gateway_client,
    )
    app.state.mcp_server = MCPServer(rpc_server=shared_rpc)
    app.state.gateway_client = gateway_client
    app.add_middleware(AuthMiddleware)
    app.add_middleware(BodySizeMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.include_router(router)

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "fusion-project-svc", "auth": "on" if config.REST_API_KEY else "off"}

    @app.get("/ready")
    async def ready():
        gw = app.state.gateway_client
        gateway_ok, rag_ok, agent_ok = await asyncio.gather(
            gw.gateway_is_healthy(),
            gw.rag_is_healthy(),
            gw.agent_studio_is_healthy(),
        )
        deps = {"gateway": gateway_ok, "rag": rag_ok, "agent_studio": agent_ok}
        all_ok = gateway_ok and rag_ok and agent_ok
        logger.info("ready check gateway=%s rag=%s agent=%s", gateway_ok, rag_ok, agent_ok)
        status_code = 200 if all_ok else 503
        return JSONResponse(
            status_code=status_code,
            content={"status": "ready" if all_ok else "degraded", "deps": deps},
        )

    @app.get("/metrics")
    async def metrics_endpoint():
        from project_service import metrics as metrics_mod
        gw = app.state.gateway_client
        gateway_ok, rag_ok, agent_ok = await asyncio.gather(
            gw.gateway_is_healthy(),
            gw.rag_is_healthy(),
            gw.agent_studio_is_healthy(),
        )
        snap = metrics_mod.snapshot()
        snap["upstream_health"] = {
            "gateway": gateway_ok,
            "rag": rag_ok,
            "agent_studio": agent_ok,
        }
        snap["auth"] = "on" if config.REST_API_KEY else "off"
        snap["rate_limit"] = {"limit": config.REST_RATE_LIMIT, "window_s": config.REST_RATE_WINDOW}
        return JSONResponse(status_code=200, content=snap)

    logger.info("FastAPI app created")
    return app


app = create_app()


def main() -> None:
    from logging.handlers import RotatingFileHandler
    fmt = logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    config.ensure_dirs()
    fh = RotatingFileHandler(
        str(config.LOG_DIR / "rest.log"),
        maxBytes=config.LOG_MAX_BYTES,
        backupCount=config.LOG_BACKUP_COUNT,
    )
    fh.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(fh)
    logger.info("starting REST on %s:%s", config.REST_HOST, config.REST_PORT)
    uvicorn.run(app, host=config.REST_HOST, port=config.REST_PORT, log_level="info")


if __name__ == "__main__":
    main()
