import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

SOCKET_PATH = os.environ.get("FUSION_PROJECT_SOCK", "/tmp/fusion-project-svc.sock")
SOCKET_MODE = int(os.environ.get("FUSION_PROJECT_SOCK_MODE", "0o600"), 8)
REST_HOST = os.environ.get("FUSION_PROJECT_HOST", "127.0.0.1")
REST_PORT = int(os.environ.get("FUSION_PROJECT_PORT", "11440"))


def rest_host_is_loopback() -> bool:
    host = REST_HOST.strip().lower()
    return host in ("127.0.0.1", "::1", "localhost", "0:0:0:0:0:0:0:1", "[::1]")

BASE_DIR = Path(os.environ.get("FUSION_PROJECT_HOME", str(Path.home() / ".fusion-projects")))
DATA_DIR = BASE_DIR / "data"
STORAGE_DIR = BASE_DIR / "storage"
DB_PATH = DATA_DIR / "projects.db"
LOG_DIR = BASE_DIR / "logs"

RAG_BASE_URL = os.environ.get("FUSION_RAG_URL", "http://127.0.0.1:11436")
RAG_EMBEDDING_MODEL = os.environ.get("FUSION_RAG_EMBEDDING_MODEL", "BAAI--bge-m3")
AGENT_STUDIO_URL = os.environ.get("FUSION_AGENT_STUDIO_URL", "http://127.0.0.1:11455")
GATEWAY_URL = os.environ.get("FUSION_GATEWAY_URL", "http://127.0.0.1:11432")
ARTIFACTS_URL = os.environ.get("FUSION_ARTIFACTS_URL", "http://127.0.0.1:8892")
CHAT_HISTORY_LIMIT = int(os.environ.get("FUSION_CHAT_HISTORY_LIMIT", "50"))

SECRET_FILE = Path(os.environ.get("FUSION_PROJECT_SECRET_FILE", str(BASE_DIR / "secret.key")))


def _read_secret_file(path: Path) -> str:
    try:
        if path.exists():
            st = path.stat()
            if st.st_mode & 0o077:
                logger.warning("secret file %s is world/group readable (mode=%o); chmod 0o600 recommended", path, st.st_mode & 0o777)
            return path.read_text(encoding="utf-8").strip()
    except Exception as e:
        logger.error("failed to read secret file %s: %s", path, e)
    return ""


REST_API_KEY = (
    os.environ.get("FUSION_REST_API_KEY", "")
    or _read_secret_file(SECRET_FILE)
)
REST_ALLOW_NO_AUTH = os.environ.get("FUSION_REST_ALLOW_NO_AUTH", "") != ""
GATEWAY_API_KEY = (
    os.environ.get("FUSION_GATEWAY_API_KEY", "")
    or os.environ.get("FUSION_MLX_API_KEY", "")
)

REST_MAX_BODY_BYTES = int(os.environ.get("FUSION_REST_MAX_BODY_BYTES", str(8 * 1024 * 1024)))
REST_RATE_LIMIT = int(os.environ.get("FUSION_REST_RATE_LIMIT", "60"))
REST_RATE_WINDOW = float(os.environ.get("FUSION_REST_RATE_WINDOW", "60"))
KNOWLEDGE_MAX_FILE_BYTES = int(os.environ.get("FUSION_KNOWLEDGE_MAX_FILE_BYTES", str(100 * 1024 * 1024)))
UDS_MAX_LINE_BYTES = int(os.environ.get("FUSION_UDS_MAX_LINE_BYTES", str(16 * 1024 * 1024)))
MCP_MAX_LINE_BYTES = int(os.environ.get("FUSION_MCP_MAX_LINE_BYTES", str(16 * 1024 * 1024)))
WAL_AUTO_CHECKPOINT = int(os.environ.get("FUSION_WAL_AUTO_CHECKPOINT", "1000"))
LOG_MAX_BYTES = int(os.environ.get("FUSION_LOG_MAX_BYTES", str(50 * 1024 * 1024)))
LOG_BACKUP_COUNT = int(os.environ.get("FUSION_LOG_BACKUP_COUNT", "5"))
KNOWLEDGE_PROJECT_QUOTA_BYTES = int(os.environ.get("FUSION_KNOWLEDGE_PROJECT_QUOTA", str(2 * 1024 * 1024 * 1024)))
KNOWLEDGE_GLOBAL_QUOTA_BYTES = int(os.environ.get("FUSION_KNOWLEDGE_GLOBAL_QUOTA", str(20 * 1024 * 1024 * 1024)))
RATE_MAX_IPS = int(os.environ.get("FUSION_RATE_MAX_IPS", "10000"))
EXPORT_INLINE_MAX_BYTES = int(os.environ.get("FUSION_EXPORT_INLINE_MAX_BYTES", str(64 * 1024 * 1024)))
EXPORT_CHUNK_BYTES = int(os.environ.get("FUSION_EXPORT_CHUNK_BYTES", str(512 * 1024)))
GATEWAY_POOL_MAX_CONN = int(os.environ.get("FUSION_GATEWAY_POOL_MAX_CONN", "100"))
GATEWAY_POOL_MAX_KEEPALIVE = int(os.environ.get("FUSION_GATEWAY_POOL_MAX_KEEPALIVE", "20"))
RAG_INDEX_CONCURRENCY = int(os.environ.get("FUSION_RAG_INDEX_CONCURRENCY", "4"))

DEFAULT_RAG_MODE = "AUTO"
DEFAULT_RAG_TOP_K = 5
DEFAULT_RAG_THRESHOLD = 0.65
DEFAULT_PROMPT_MERGE = "AGENT_FIRST"
MAX_INSTRUCTION_CHARS = 10000


def ensure_dirs() -> None:
    for d in (DATA_DIR, STORAGE_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
    logger.info("fusion-project-svc dirs ready under %s", BASE_DIR)
