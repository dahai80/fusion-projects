import logging
from typing import Optional

import uvicorn
from fastapi import FastAPI

from project_service import config
from project_service.api.routes import router
from project_service.engine.instruction_engine import InstructionEngine
from project_service.engine.project_manager import ProjectManager
from project_service.mcp_server import MCPServer

logger = logging.getLogger(__name__)


def create_app(
    project_manager: Optional[ProjectManager] = None,
    instruction_engine: Optional[InstructionEngine] = None,
) -> FastAPI:
    app = FastAPI(title="Fusion-Projects", version="0.2.0")
    app.state.project_manager = project_manager or ProjectManager()
    app.state.instruction_engine = instruction_engine or InstructionEngine(
        project_manager=app.state.project_manager
    )
    app.state.mcp_server = MCPServer()
    app.include_router(router)

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "fusion-project-svc"}

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
