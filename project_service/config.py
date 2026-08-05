import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

SOCKET_PATH = os.environ.get("FUSION_PROJECT_SOCK", "/tmp/fusion-project-svc.sock")
REST_HOST = os.environ.get("FUSION_PROJECT_HOST", "127.0.0.1")
REST_PORT = int(os.environ.get("FUSION_PROJECT_PORT", "11440"))

BASE_DIR = Path(os.environ.get("FUSION_PROJECT_HOME", str(Path.home() / ".fusion-projects")))
DATA_DIR = BASE_DIR / "data"
STORAGE_DIR = BASE_DIR / "storage"
DB_PATH = DATA_DIR / "projects.db"
LOG_DIR = BASE_DIR / "logs"

RAG_BASE_URL = os.environ.get("FUSION_RAG_URL", "http://127.0.0.1:11436")
RAG_EMBEDDING_MODEL = os.environ.get("FUSION_RAG_EMBEDDING_MODEL", "BAAI--bge-m3")
AGENT_STUDIO_URL = os.environ.get("FUSION_AGENT_STUDIO_URL", "http://127.0.0.1:8000")
GATEWAY_URL = os.environ.get("FUSION_GATEWAY_URL", "http://127.0.0.1:11432")
GATEWAY_API_KEY = os.environ.get("FUSION_GATEWAY_API_KEY", "") or os.environ.get("FUSION_MLX_API_KEY", "")

DEFAULT_RAG_MODE = "AUTO"
DEFAULT_RAG_TOP_K = 5
DEFAULT_RAG_THRESHOLD = 0.65
DEFAULT_PROMPT_MERGE = "AGENT_FIRST"
MAX_INSTRUCTION_CHARS = 10000


def ensure_dirs() -> None:
    for d in (DATA_DIR, STORAGE_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
    logger.info("fusion-project-svc dirs ready under %s", BASE_DIR)
