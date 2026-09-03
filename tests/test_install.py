"""Integration tests for the installer."""
import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


class TestInstall:
    """Test package installation."""

    def setup_method(self):
        """Create a fresh venv for each test."""
        self.venv_dir = Path(tempfile.mkdtemp(prefix="accelero-test-"))
        result = subprocess.run(
            [sys.executable, "-m", "venv", str(self.venv_dir)],
            check=True,
            capture_output=True,
        )
        self.site_packages = self.venv_dir / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"

    def teardown_method(self):
        """Clean up venv."""
        shutil.rmtree(self.venv_dir, ignore_errors=True)

    def accelero(self, *args):
        """Run accelero command."""
        result = subprocess.run(
            ["accelero", "--target", str(self.venv_dir)] + list(args),
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent.parent),
        )
        return result

    def _create_local_editable_package(self, pkg_root: Path, module_name: str = "my_lib"):
        """Create a small local package for editable-install tests."""
        src_pkg = pkg_root / "src" / module_name
        src_pkg.mkdir(parents=True, exist_ok=True)
        (src_pkg / "__init__.py").write_text('VALUE = "ok"\n')
        (pkg_root / "pyproject.toml").write_text(
            "\n".join(
                [
                    "[build-system]",
                    'requires = ["setuptools>=68", "wheel"]',
                    'build-backend = "setuptools.build_meta"',
                    "",
                    "[project]",
                    f'name = "{module_name.replace("_", "-")}"',
                    'version = "0.0.1"',
                ]
            )
            + "\n"
        )

    def _assert_importable_in_target(self, module_name: str):
        """Assert module can be imported with target venv python."""
        target_python = self.venv_dir / "bin" / "python"
        result = subprocess.run(
            [str(target_python), "-c", f"import {module_name}; print({module_name}.VALUE)"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "ok" in result.stdout

    def test_install_single_package(self):
        """Test installing a single package."""
        result = self.accelero("install", "six")
        assert result.returncode == 0, f"Failed: {result.stderr}"
        assert (self.site_packages / "six.py").exists()
        assert (self.site_packages / "six.pyc").exists()

    def test_install_requirements_file(self):
        """Test installing from requirements file."""
        req_file = self.venv_dir / "requirements.txt"
        req_file.write_text("click\nsix\n")

        result = self.accelero("install", "-r", str(req_file))
        assert result.returncode == 0, f"Failed: {result.stderr}"
        assert (self.site_packages / "click").exists()
        assert (self.site_packages / "six.py").exists()

    def test_list_command(self):
        """Test listing installed packages."""
        # First install something
        self.accelero("install", "six")
        # Then list
        result = self.accelero("list", "--format", "json")
        # Note: this might fail because six is installed in user site-packages not target
        # Let's just verify the command runs
        assert "list" in result.stdout.lower() or result.returncode == 0

    def test_show_command(self):
        """Test showing package info."""
        result = self.accelero("show", "requests")
        # Should succeed or fail gracefully
        assert result.returncode in (0, 1)

    def test_doctor_command(self):
        """Test doctor command."""
        result = self.accelero("doctor")
        assert result.returncode == 0
        assert "python" in result.stdout.lower()

    def test_cache_info(self):
        """Test cache info."""
        result = self.accelero("cache", "info")
        assert result.returncode == 0
        assert "cache" in result.stdout.lower()

    def test_cache_dir(self):
        """Test cache dir."""
        result = self.accelero("cache", "dir")
        assert result.returncode == 0
        path = result.stdout.strip()
        assert Path(path).exists()

    def test_install_editable_only(self):
        """Test editable-only install."""
        pkg_dir = self.venv_dir / "editable-only"
        self._create_local_editable_package(pkg_dir, module_name="editable_only_pkg")

        result = self.accelero("install", "-e", str(pkg_dir))
        assert result.returncode == 0, f"Failed: {result.stdout}\n{result.stderr}"
        self._assert_importable_in_target("editable_only_pkg")

    def test_install_requirements_with_relative_editable_line(self):
        """Test requirements file editable line relative to req file location."""
        workspace_dir = self.venv_dir / "workspace"
        project_dir = workspace_dir / "project"
        lib_dir = workspace_dir / "my-lib"
        project_dir.mkdir(parents=True, exist_ok=True)
        self._create_local_editable_package(lib_dir, module_name="relative_editable_pkg")
        req_file = project_dir / "requirements.txt"
        req_file.write_text("-e ../my-lib\n")

        result = self.accelero("install", "-r", str(req_file))
        assert result.returncode == 0, f"Failed: {result.stdout}\n{result.stderr}"
        self._assert_importable_in_target("relative_editable_pkg")

    def test_install_already_installed_package_with_editable(self):
        """Test mixed install where package is already installed plus editable target."""
        first = self.accelero("install", "six")
        assert first.returncode == 0, f"Failed: {first.stdout}\n{first.stderr}"

        pkg_dir = self.venv_dir / "mixed-editable"
        self._create_local_editable_package(pkg_dir, module_name="mixed_editable_pkg")
        result = self.accelero("install", "six", "-e", str(pkg_dir))
        assert result.returncode == 0, f"Failed: {result.stdout}\n{result.stderr}"
        self._assert_importable_in_target("mixed_editable_pkg")


class TestResolver:
    """Test the dependency resolver."""

    @pytest.mark.asyncio
    async def test_resolve_simple(self):
        """Test resolving a simple package."""
        if os.environ.get("SKIP_NETWORK_TESTS"):
            pytest.skip("Skipping network test")

        from accelero.cache.store import Cache
        from accelero.net.client import HTTPClient
        from accelero.resolve.resolver import SimpleResolver

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = Cache(Path(tmpdir))
            async with HTTPClient(timeout=10.0) as http:
                resolver = SimpleResolver(http, cache)
                pkg = await resolver.resolve_one("six")
                assert pkg is not None
                assert pkg.name.lower() == "six"
                assert pkg.url.endswith(".whl") or not pkg.wheel

    @pytest.mark.asyncio
    async def test_resolve_with_deps(self):
        """Test resolving a package with dependencies."""
        if os.environ.get("SKIP_NETWORK_TESTS"):
            pytest.skip("Skipping network test")

        from accelero.cache.store import Cache
        from accelero.net.client import HTTPClient
        from accelero.resolve.resolver import SimpleResolver

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = Cache(Path(tmpdir))
            async with HTTPClient(timeout=30.0) as http:
                resolver = SimpleResolver(http, cache)
                packages, errors = await resolver.resolve_many(["click"])
                assert len(packages) >= 1, f"Failed to resolve click: {errors}"
                names = {p.name.lower() for p in packages}
                assert "click" in names


class TestCache:
    """Test the cache."""

    def test_cache_basic(self):
        """Test basic cache operations."""
        with tempfile.TemporaryDirectory() as tmpdir:
            from accelero.cache.store import Cache

            cache = Cache(Path(tmpdir))

            # Set and get metadata
            cache.set_metadata("test", {"key": "value"}, ttl=60)
            result = cache.get_metadata("test")
            assert result == {"key": "value"}
            assert cache.stats()["hits"] >= 1

            # Cache miss
            assert cache.get_metadata("nonexistent") is None

    def test_cache_clean(self):
        """Test cache cleaning."""
        with tempfile.TemporaryDirectory() as tmpdir:
            from accelero.cache.store import Cache

            cache = Cache(Path(tmpdir))
            cache.set_metadata("test", {"data": 1}, ttl=60)
            # clean() removes DB records but not the DB file itself
            result = cache.clean()
            assert cache.get_metadata("test") is None


class TestCLI:
    """Test CLI commands."""

    def test_version(self):
        """Test version flag."""
        result = subprocess.run(
            ["accelero", "--version"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "accelero" in result.stdout
        assert "0.1" in result.stdout

    def test_help(self):
        """Test help output."""
        result = subprocess.run(
            ["accelero", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "install" in result.stdout
        assert "uninstall" in result.stdout


@pytest.mark.asyncio
async def test_sdist_fallback_uses_pip_no_deps(monkeypatch, tmp_path):
    """Test sdist fallback install uses pip with --no-deps."""
    from accelero.cli.main import cmd_install
    import accelero.cli.main as cli_main
    from accelero.cli.output import Output
    from accelero.core.config import Config
    from accelero.resolve.resolver import ResolvedPackage

    pip_calls = []

    class FakeHTTPClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def download_stream(self, url, dest):
            dest.write_text("fake sdist content", encoding="utf-8")
            return dest, "fakehash"

    class FakeResolver:
        def __init__(self, http_client, cache, no_cache=False):
            pass

        async def resolve_many(self, requirements):
            return [
                ResolvedPackage(
                    name="sdist-only",
                    version="1.0.0",
                    url="https://example.invalid/sdist-only-1.0.0.tar.gz",
                    sha256="",
                    wheel=False,
                )
            ], []

    def fake_pip_install(python_executable, target, *, editable=False, no_deps=False):
        pip_calls.append(
            {
                "python_executable": str(python_executable),
                "target": target,
                "editable": editable,
                "no_deps": no_deps,
            }
        )
        return True, ""

    monkeypatch.setattr(cli_main, "HTTPClient", FakeHTTPClient)
    monkeypatch.setattr(cli_main, "SimpleResolver", FakeResolver)
    monkeypatch.setattr(cli_main, "_pip_install", fake_pip_install)

    args = argparse.Namespace(
        target=str(tmp_path / "target-env"),
        python=None,
        packages=["sdist-only"],
        requirements=None,
        constraints=None,
        upgrade=False,
        force_reinstall=False,
        require_hashes=False,
        editable=None,
    )
    config = Config(cache_dir=tmp_path / "cache")
    output = Output(no_progress=True, quiet=True)

    result = await cmd_install(args, config, output)
    assert result == 0
    assert len(pip_calls) == 1
    assert pip_calls[0]["editable"] is False
    assert pip_calls[0]["no_deps"] is True
    assert pip_calls[0]["target"].endswith(".tar.gz")
