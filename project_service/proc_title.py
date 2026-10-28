import logging

logger = logging.getLogger(__name__)


def set_process_title(title: str) -> None:
    # issue #33: daemon must show the product name in ps/Activity Monitor, not
    # bare homebrew "Python". fusion_core.proc.set_process_title wraps
    # setproctitle with a safe try/except; this shim keeps the same fail-soft
    # contract when fusion-core (optional extra) is absent.
    try:
        from fusion_core.proc import set_process_title as _fc_set
        _fc_set(title)
        return
    except Exception:
        pass
    try:
        import setproctitle

        setproctitle.setproctitle(title)
        logger.debug("process title set: %s", title)
    except Exception as exc:
        logger.debug("setproctitle unavailable, process title stays default (%s): %s", title, exc)
