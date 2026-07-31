import logging
import shutil
from pathlib import Path
from typing import Optional

from project_service import config

logger = logging.getLogger(__name__)

PROJECT_SUBDIRS = ("knowledge", "attachments", "snapshots", "exports")


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
