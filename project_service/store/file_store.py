import logging
import os
import shutil
from pathlib import Path
from typing import Optional

from project_service import config

logger = logging.getLogger(__name__)

PROJECT_SUBDIRS = ("knowledge", "attachments", "snapshots", "exports")


class QuotaExceeded(Exception):
    pass


class FileStore:
    def __init__(self, storage_dir: Optional[Path] = None) -> None:
        self.storage_dir = Path(storage_dir) if storage_dir else config.STORAGE_DIR
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        logger.info("FileStore ready storage=%s", self.storage_dir)

    def _project_dir(self, project_id: str) -> Path:
        return self.storage_dir / project_id

    def project_dir(self, project_id: str) -> Path:
        return self._project_dir(project_id)

    def init_project(self, project_id: str) -> Path:
        pdir = self._project_dir(project_id)
        for sub in PROJECT_SUBDIRS:
            (pdir / sub).mkdir(parents=True, exist_ok=True)
        logger.info("init project storage id=%s dir=%s", project_id, pdir)
        return pdir

    def remove_project(self, project_id: str) -> bool:
        pdir = self._project_dir(project_id)
        if pdir.exists():
            shutil.rmtree(pdir)
            logger.info("removed project storage id=%s", project_id)
            return True
        return False

    def has_project(self, project_id: str) -> bool:
        return self._project_dir(project_id).exists()

    def project_usage_bytes(self, project_id: str) -> int:
        pdir = self._project_dir(project_id)
        if not pdir.exists():
            return 0
        total = 0
        for root, _dirs, files in os.walk(pdir):
            for f in files:
                try:
                    fp = Path(root) / f
                    if not fp.is_symlink():
                        total += fp.stat().st_size
                except OSError as e:
                    logger.warning("project_usage stat failed path=%s err=%s", fp, e)
        return total

    def global_usage_bytes(self) -> int:
        if not self.storage_dir.exists():
            return 0
        total = 0
        for root, _dirs, files in os.walk(self.storage_dir):
            for f in files:
                try:
                    fp = Path(root) / f
                    if not fp.is_symlink():
                        total += fp.stat().st_size
                except OSError as e:
                    logger.warning("global_usage stat failed path=%s err=%s", fp, e)
        return total

    def check_quota(self, project_id: str, add_bytes: int) -> None:
        project_quota = config.KNOWLEDGE_PROJECT_QUOTA_BYTES
        global_quota = config.KNOWLEDGE_GLOBAL_QUOTA_BYTES
        if project_quota > 0:
            used = self.project_usage_bytes(project_id)
            if used + add_bytes > project_quota:
                raise QuotaExceeded(
                    f"project quota exceeded: project={project_id} used={used} add={add_bytes} limit={project_quota}"
                )
        if global_quota > 0:
            gused = self.global_usage_bytes()
            if gused + add_bytes > global_quota:
                raise QuotaExceeded(
                    f"global quota exceeded: global_used={gused} add={add_bytes} limit={global_quota}"
                )
