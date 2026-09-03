"""Basic tests for Holo Accelerant."""
import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def test_version():
    """Test that version is defined."""
    from accelero import __version__
    assert __version__ is not None
    assert isinstance(__version__, str)


def test_paths():
    """Test path utilities."""
    from accelero.utils.paths import get_cache_dir, get_python_version, is_venv

    assert isinstance(get_cache_dir(), Path)
    assert isinstance(get_python_version(), str)
    assert get_python_version().count(".") == 1
    assert isinstance(is_venv(), bool)


def test_config():
    """Test configuration."""
    from accelero.core.config import Config, load_config

    config = Config()
    assert config.index_url is not None
    assert config.max_concurrent > 0

    loaded = load_config()
    assert loaded.index_url is not None


def test_environment():
    """Test environment detection."""
    from accelero.core.environment import detect_environment

    env = detect_environment()
    assert env.python_version is not None
    assert env.site_packages is not None
    assert env.platform in ("linux", "darwin", "win32", "cygwin")


def test_timings():
    """Test timings utility."""
    from accelero.utils.logging import Timings

    timings = Timings()
    with timings.phase("test"):
        import time
        time.sleep(0.1)

    assert "test" in timings.phases
    assert timings.phases["test"] >= 0.1
    assert timings.total() >= 0.1


def test_cli_help():
    """Test that the CLI can be invoked."""
    from accelero.cli.main import build_parser

    parser = build_parser()
    assert parser is not None

    # Test help works
    try:
        parser.parse_args(["--help"])
    except SystemExit:
        pass  # Help exits successfully


@pytest.mark.asyncio
async def test_http_client():
    """Test HTTP client (only runs with network)."""
    from accelero.net.client import HTTPClient

    if os.environ.get("SKIP_NETWORK_TESTS"):
        pytest.skip("Skipping network test")

    async with HTTPClient(timeout=10.0) as client:
        response = await client.get("https://pypi.org/pypi/requests/json")
        assert response.status == 200
        assert len(response.content) > 0


def test_cache():
    """Test cache."""
    import tempfile
    from accelero.cache.store import Cache

    with tempfile.TemporaryDirectory() as tmpdir:
        cache = Cache(Path(tmpdir))
        cache.set_metadata("test_key", {"value": 42}, ttl=60)
        result = cache.get_metadata("test_key")
        assert result == {"value": 42}

        stats = cache.stats()
        assert "wheels_cached" in stats
        assert stats["hits"] >= 1
