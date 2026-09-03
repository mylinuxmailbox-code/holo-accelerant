"""
Environment detection and management.

Handles:
- Virtual environment detection
- Target directory determination
- Python executable discovery
- Site-packages location
"""
from __future__ import annotations

import os
import site
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from accelero.utils.paths import is_venv


@dataclass
class Environment:
    """Detected Python environment information."""

    python_executable: Path
    python_version: str
    platform: str
    architecture: str
    is_virtualenv: bool
    target_dir: Path
    site_packages: Path
    scripts_dir: Path
    purelib_dir: Path
    sys_path: list[str]

    @property
    def site_packages_str(self) -> str:
        return str(self.site_packages)


def detect_environment(
    python: Optional[str] = None,
    target: Optional[Path] = None,
) -> Environment:
    """
    Detect the current Python environment.

    Args:
        python: Optional path to Python interpreter
        target: Optional target directory for installation

    Returns:
        Environment with detected paths and information
    """
    # Determine Python executable
    if python:
        python_path = Path(python).resolve()
        if not python_path.exists():
            raise FileNotFoundError(f"Python executable not found: {python}")
    else:
        python_path = Path(sys.executable)

    # Get Python version
    version = f"{sys.version_info.major}.{sys.version_info.minor}"

    # Detect platform
    import platform

    plat = sys.platform
    arch = platform.machine()

    # Determine target directory
    if target:
        target_dir = target.resolve()
    elif is_venv():
        # Check VIRTUAL_ENV first (set by venv activation)
        venv_env = os.environ.get("VIRTUAL_ENV")
        if venv_env:
            target_dir = Path(venv_env)
        else:
            target_dir = Path(sys.prefix)
    else:
        # User site-packages
        user_base = site.getuserbase()
        target_dir = Path(user_base) if user_base else Path.home() / ".local"

    # Calculate site-packages and scripts directories
    if is_venv() or target:
        # In a venv or explicit target
        site_packages = target_dir / "lib" / f"python{version}" / "site-packages"
        scripts = target_dir / "bin"
        purelib = site_packages
    else:
        # Global/user installation
        site_packages = Path(site.getusersitepackages()) if site.getusersitepackages() else target_dir / "lib" / "site-packages"
        scripts = target_dir / "bin"

    return Environment(
        python_executable=python_path,
        python_version=version,
        platform=plat,
        architecture=arch,
        is_virtualenv=is_venv(),
        target_dir=target_dir,
        site_packages=site_packages,
        scripts_dir=scripts,
        purelib_dir=site_packages,
        sys_path=list(sys.path),
    )


def get_site_packages() -> Path:
    """Return the site-packages directory for the current environment."""
    if is_venv():
        return Path(sys.prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    return Path(site.getusersitepackages())


def get_scripts_dir() -> Path:
    """Return the scripts/bin directory for the current environment."""
    if is_venv():
        return Path(sys.prefix) / "bin"
    user_base = site.getuserbase()
    return Path(user_base) / "bin" if user_base else Path.home() / ".local" / "bin"


def get_python_info() -> dict:
    """Return detailed Python information."""
    import platform

    return {
        "executable": sys.executable,
        "version": sys.version,
        "version_info": {
            "major": sys.version_info.major,
            "minor": sys.version_info.minor,
            "micro": sys.version_info.micro,
            "releaselevel": sys.version_info.releaselevel,
        },
        "platform": sys.platform,
        "platform_release": platform.release(),
        "platform_version": platform.version(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "implementation": platform.python_implementation(),
        "is_virtualenv": is_venv(),
        "sys_prefix": sys.prefix,
        "sys_base_prefix": sys.base_prefix,
        "site_packages": get_site_packages(),
    }
