"""Path and platform utilities."""
from __future__ import annotations

import os
import platform
import sys
from pathlib import Path


def get_platform_tag() -> str:
    """Return the platform tag for the current system (PEP 425)."""
    if sys.platform.startswith("linux"):
        return "linux_x86_64" if platform.machine() == "x86_64" else f"linux_{platform.machine()}"
    elif sys.platform == "darwin":
        machine = platform.machine()
        if machine == "x86_64":
            return "macosx_10_9_x86_64"
        return "macosx_11_0_arm64" if machine == "arm64" else f"macosx_{machine}"
    elif sys.platform in ("win32", "cygwin"):
        return "win_amd64" if platform.machine() in ("AMD64", "x86_64") else f"win_{platform.machine()}"
    return sys.platform


def get_python_version() -> str:
    """Return Python version as 'major.minor' (e.g. '3.10')."""
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def get_python_tag() -> str:
    """Return PEP 425 python tag (e.g. 'py3', 'cp310')."""
    impl = platform.python_implementation().lower()
    major = sys.version_info.major
    minor = sys.version_info.minor
    if impl == "cpython":
        return f"cp{major}{minor}"
    return f"py{major}{minor}"


def get_abi_tag() -> str:
    """Return PEP 425 ABI tag."""
    impl = platform.python_implementation().lower()
    if impl == "cpython":
        return f"cp{sys.version_info.major}{sys.version_info.minor}"
    return "none"


def is_venv() -> bool:
    """Return True if running inside a virtual environment."""
    if sys.prefix != sys.base_prefix or hasattr(sys, "real_prefix"):
        return True
    # Also check common env vars set by venv activation
    if os.environ.get("VIRTUAL_ENV"):
        return True
    if os.environ.get("CONDA_DEFAULT_ENV"):
        return True
    return False


def get_venv_root() -> Path | None:
    """Return the root of the active virtual environment or None."""
    if is_venv():
        return Path(sys.prefix)
    return None


def get_cache_dir() -> Path:
    """Return the user-level cache directory for accelero."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "accelero" / "cache"
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "accelero"
    return Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "accelero"


def get_config_dir() -> Path:
    """Return the user-level config directory for accelero."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "accelero"
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "accelero"


def get_data_dir() -> Path:
    """Return the user-level data directory for accelero."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "accelero" / "data"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "accelero"
