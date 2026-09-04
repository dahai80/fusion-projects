import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from project_service import config

logger = logging.getLogger(__name__)


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        import json as _json
        from datetime import UTC, datetime

        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "name": record.name,
            "msg": record.getMessage(),
        }
        try:
            from fusion_core.tenant.context import current as _tenant_current

            ctx = _tenant_current()
        except Exception:
            ctx = None
        if ctx is not None:
            payload["tenant_id"] = ctx.tenant_id
            if ctx.user_id is not None:
                payload["user_id"] = ctx.user_id
        # M11: per-request correlation id (UDS dispatch sets it per request).
        try:
            from project_service.daemon_server import request_id_var
            rid = request_id_var.get()
            if rid and rid != "-":
                payload["request_id"] = rid
        except Exception:
            pass
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return _json.dumps(payload, ensure_ascii=False)


def setup_logging(log_file: str | Path, *, level: str = "INFO") -> logging.Logger:
    config.ensure_dirs()
    use_json = os.environ.get("FUSION_LOG_JSON", "0") == "1"
    if use_json:
        fmt = _JsonFormatter()
    else:
        fmt = logging.Formatter(
            "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
        )
        # M11: attach a filter that appends request_id (set per UDS request) so
        # plain-text logs also carry the correlation id without format-string churn.
        class _RidFilter(logging.Filter):
            def filter(self, record: logging.LogRecord) -> bool:
                try:
                    from project_service.daemon_server import request_id_var
                    rid = request_id_var.get()
                except Exception:
                    rid = "-"
                record.__dict__["request_id"] = rid if rid and rid != "-" else "-"
                return True
        rid_filter = _RidFilter()
    level_attr = getattr(logging, level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level_attr)
    fh = RotatingFileHandler(
        str(log_file),
        maxBytes=config.LOG_MAX_BYTES,
        backupCount=config.LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    if not use_json:
        fh.addFilter(rid_filter)
    root.addHandler(fh)
    logger.info("logging setup json=%s file=%s level=%s", use_json, log_file, level)
    return root
