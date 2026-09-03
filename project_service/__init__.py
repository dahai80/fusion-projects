__app_name__ = "Fusion-Projects"

try:
    from importlib.metadata import version as _pkg_version, PackageNotFoundError

    try:
        __version__ = _pkg_version("fusion-project-svc")
    except PackageNotFoundError:
        __version__ = "0.0.0+unknown"
except Exception:
    __version__ = "0.0.0+unknown"
