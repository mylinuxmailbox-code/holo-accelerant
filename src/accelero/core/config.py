"""
Configuration management.

Supports:
- CLI arguments (highest priority)
- Environment variables
- Project configuration (pyproject.toml, requirements.txt)
- User configuration (~/.config/accelero/config.toml)
- System configuration
- Defaults (lowest priority)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from accelero.utils.paths import get_config_dir

DEFAULT_INDEX_URL = "https://pypi.org/simple/"
DEFAULT_TRUSTED_HOSTS = ["pypi.org", "files.pythonhosted.org"]


@dataclass
class Config:
    """Configuration for accelero operations."""

    # Index configuration
    index_url: str = DEFAULT_INDEX_URL
    extra_index_urls: list[str] = field(default_factory=list)
    trusted_hosts: list[str] = field(default_factory=lambda: list(DEFAULT_TRUSTED_HOSTS))
    no_index: bool = False

    # Network configuration
    timeout: float = 30.0
    max_retries: int = 3
    max_concurrent: int = 32

    # Cache configuration
    cache_dir: Path | None = None
    no_cache: bool = False
    cache_ttl: float = 3600.0

    # Installation configuration
    target: Path | None = None
    python: str | None = None
    force_reinstall: bool = False
    upgrade: bool = False
    only_binary: list[str] = field(default_factory=list)
    no_binary: list[str] = field(default_factory=list)

    # Behavior flags
    verbose: bool = False
    quiet: bool = False
    dry_run: bool = False
    offline: bool = False
    require_hashes: bool = False

    # Output configuration
    progress: bool = True
    color: bool = True

    def merge_env(self) -> None:
        """Merge environment variables into config."""
        # Index configuration
        if os.environ.get("PIP_INDEX_URL"):
            self.index_url = os.environ["PIP_INDEX_URL"]
        if os.environ.get("PIP_EXTRA_INDEX_URL"):
            self.extra_index_urls = os.environ["PIP_EXTRA_INDEX_URL"].split()

        # Cache
        if os.environ.get("PIP_CACHE_DIR"):
            self.cache_dir = Path(os.environ["PIP_CACHE_DIR"])

        # Network
        if os.environ.get("ACCELERO_TIMEOUT"):
            self.timeout = float(os.environ["ACCELERO_TIMEOUT"])

        # Behavior
        if os.environ.get("PIP_NO_INPUT"):
            self.quiet = True
        if os.environ.get("ACCELERO_OFFLINE"):
            self.offline = True
        if os.environ.get("ACCELERO_NO_CACHE"):
            self.no_cache = True

    def merge_file(self, config_file: Path) -> None:
        """Merge configuration from TOML file."""
        try:
            import tomllib

            with open(config_file, "rb") as f:
                data = tomllib.load(f)
        except ImportError:
            import tomli

            with open(config_file, "rb") as f:
                data = tomli.load(f)

        if "tool" in data and "accelero" in data["tool"]:
            conf = data["tool"]["accelero"]

            def set_field(key: str, value: Any) -> None:
                if hasattr(self, key) and key in conf:
                    setattr(self, key, conf[key])

            set_field("index_url", conf.get("index-url"))
            set_field("extra_index_urls", conf.get("extra-index-urls", []))
            set_field("trusted_hosts", conf.get("trusted-hosts", DEFAULT_TRUSTED_HOSTS))
            set_field("no_cache", conf.get("no-cache", False))
            set_field("cache_ttl", conf.get("cache-ttl", 3600.0))
            set_field("max_concurrent", conf.get("max-concurrent", 32))


def load_config(
    config_file: Path | None = None,
    project_file: Path | None = None,
) -> Config:
    """Load configuration from all sources."""
    config = Config()

    # 1. Load from user config file
    user_config = get_config_dir() / "config.toml"
    if user_config.exists():
        config.merge_file(user_config)

    # 2. Load from project config
    if project_file and project_file.exists():
        config.merge_file(project_file)
    else:
        # Look for pyproject.toml in current directory
        cwd = Path.cwd()
        for name in ("pyproject.toml", "accelero.toml"):
            p = cwd / name
            if p.exists():
                config.merge_file(p)
                break

    # 3. Load from environment
    config.merge_env()

    # 4. CLI args override (handled separately in CLI layer)

    return config


def get_default_config() -> Config:
    """Return default configuration."""
    return Config()
