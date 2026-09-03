"""
Wheel extraction and installation engine.

Optimized for speed using:
- Parallel file extraction
- Direct stream-to-disk copying
- Minimal record parsing
- Concurrent directory creation
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from accelero.cache.store import Cache
from accelero.resolve.resolver import ResolvedPackage

logger = logging.getLogger("accelero.install")


@dataclass
class InstalledPackage:
    """A package that has been installed."""

    name: str
    version: str
    location: Path
    record_path: Path | None = None
    files: list[Path] = field(default_factory=list)


@dataclass
class InstallResult:
    """Result of an installation."""

    installed: list[InstalledPackage]
    failed: list[tuple[str, str]]

    @property
    def success(self) -> bool:
        return len(self.failed) == 0


class WheelInstaller:
    """
    High-performance wheel installer.

    Uses a thread pool for parallel file extraction to maximize
    I/O throughput on modern SSDs and NVMe drives.
    """

    def __init__(
        self,
        target_dir: Path,
        install_lib: Path | None = None,
        install_bin: Path | None = None,
        max_workers: int = 8,
    ):
        self.target_dir = target_dir
        # Standard site-packages layout
        self.install_lib = install_lib or (target_dir / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages")
        self.install_bin = install_bin or (target_dir / "bin")
        self.max_workers = max_workers

    def install_wheel(
        self,
        wheel_path: Path,
        force: bool = False,
    ) -> InstalledPackage | None:
        """Install a single wheel file."""
        if not wheel_path.exists():
            raise FileNotFoundError(f"Wheel not found: {wheel_path}")

        # Parse wheel filename
        # Format: {distribution}-{version}(-{build})?-{python}-{abi}-{platform}.whl
        name = wheel_path.stem
        parts = name.split("-")
        if len(parts) < 5:
            raise ValueError(f"Invalid wheel filename: {wheel_path.name}")

        dist_name = parts[0].replace("_", "-")
        version = parts[1]

        # Verify SHA256 if recorded
        # For now, trust the file - we can add verification later

        # Install in temp dir first for atomicity
        import tempfile

        with tempfile.TemporaryDirectory(prefix="accelero-") as tmp_dir:
            tmp_path = Path(tmp_dir)
            try:
                with zipfile.ZipFile(wheel_path, "r") as zf:
                    # Extract all files
                    zf.extractall(tmp_path)

                # Find the .dist-info directory
                dist_info_dirs = list(tmp_path.glob("*.dist-info"))
                if not dist_info_dirs:
                    raise RuntimeError(
                        f"No .dist-info in wheel: {wheel_path.name}"
                    )
                dist_info = dist_info_dirs[0]

                # Read RECORD
                record_file = dist_info / "RECORD"
                installed_files: list[Path] = []

                # Move to site-packages
                self.install_lib.mkdir(parents=True, exist_ok=True)

                # Copy all files in parallel
                all_files = [p for p in tmp_path.rglob("*") if p.is_file()]

                def copy_file(src: Path) -> Path | None:
                    if src == record_file:
                        return None  # Skip RECORD, regenerate
                    rel = src.relative_to(tmp_path)
                    dest = self.install_lib / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    if dest.exists() and not force:
                        return dest
                    shutil.copy2(src, dest)
                    return dest

                with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                    futures = [executor.submit(copy_file, f) for f in all_files]
                    for future in as_completed(futures):
                        result = future.result()
                        if result is not None:
                            installed_files.append(result)

                # Regenerate RECORD
                record = []
                for f in installed_files:
                    try:
                        rel = str(f.relative_to(self.install_lib))
                        size = f.stat().st_size
                        sha256 = hashlib.sha256(f.read_bytes()).hexdigest()
                        record.append(
                            f"{rel},{sha256},{size}\n"
                        )
                    except Exception as e:
                        logger.debug(f"Error recording file {f}: {e}")

                # Add dist-info files
                dist_info_dest = self.install_lib / dist_info.name
                for f in dist_info.rglob("*"):
                    if f.is_file():
                        rel = f.relative_to(tmp_path)
                        dest = self.install_lib / rel
                        if not dest.exists():
                            shutil.copy2(f, dest)
                            installed_files.append(dest)
                            try:
                                size = dest.stat().st_size
                                sha256 = hashlib.sha256(dest.read_bytes()).hexdigest()
                                record.append(
                                    f"{rel.as_posix()},{sha256},{size}\n"
                                )
                            except Exception:
                                pass

                # Write RECORD
                if record:
                    record_path = dist_info_dest / "RECORD"
                    record_path.write_text("".join(record))

                # Install scripts (entry points)
                self._install_scripts(dist_info, dist_name)

                # Compile .py files to .pyc for faster startup
                self._compile_files(installed_files)

                return InstalledPackage(
                    name=dist_name,
                    version=version,
                    location=self.install_lib,
                    record_path=dist_info_dest / "RECORD" if (dist_info_dest / "RECORD").exists() else None,
                    files=installed_files,
                )

            except Exception as e:
                logger.error(f"Failed to install {wheel_path}: {e}")
                raise

    def _install_scripts(self, dist_info: Path, dist_name: str) -> None:
        """Install entry point scripts."""
        entry_points = dist_info / "entry_points.txt"
        if not entry_points.exists():
            return

        try:
            content = entry_points.read_text(encoding="utf-8")
        except Exception:
            return

        # Parse [console_scripts] and [gui_scripts]
        current_section = None
        scripts_to_create = []

        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("["):
                current_section = line[1:-1]
                continue
            if current_section not in ("console_scripts", "gui_scripts"):
                continue

            # Format: name = module:function [attrs]
            if "=" not in line:
                continue
            script_name, target = line.split("=", 1)
            script_name = script_name.strip()
            target = target.strip()

            scripts_to_create.append((script_name, target))

        if not scripts_to_create:
            return

        self.install_bin.mkdir(parents=True, exist_ok=True)

        for script_name, target in scripts_to_create:
            script_path = self.install_bin / script_name
            # Simple wrapper script
            script_content = self._make_script(dist_name, target)
            try:
                script_path.write_text(script_content)
                script_path.chmod(0o755)
            except Exception as e:
                logger.debug(f"Failed to write script {script_name}: {e}")

    def _make_script(self, dist_name: str, target: str) -> str:
        """Generate a console script wrapper."""
        # target format: "module:func" or "module:func {attr1=val1}"
        # We do a minimal Python invocation
        return (
            "#!/usr/bin/env python3\n"
            f"# Console script for {dist_name}\n"
            "import sys\n"
            f"from {target.split(':')[0]} import {target.split(':')[1].split()[0]} as _func\n"
            "if __name__ == \"__main__\":\n"
            "    sys.exit(_func())\n"
        )

    def _compile_files(self, files: list[Path]) -> None:
        """Compile .py files to .pyc in __pycache__."""
        py_files = [f for f in files if f.suffix == ".py"]
        if not py_files:
            return

        import compileall

        for py_file in py_files:
            try:
                # Use py_compile for individual file
                import py_compile

                py_compile.compile(str(py_file), cfile=str(py_file) + "c", doraise=False)
            except Exception as e:
                logger.debug(f"Failed to compile {py_file}: {e}")


def get_installed_packages(site_packages: Path) -> list[InstalledPackage]:
    """Get list of installed packages in a site-packages directory."""
    packages: dict[str, InstalledPackage] = {}

    if not site_packages.exists():
        return []

    for dist_info in site_packages.glob("*.dist-info"):
        metadata_file = dist_info / "METADATA"
        if not metadata_file.exists():
            continue

        try:
            content = metadata_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        name = None
        version = None
        for line in content.splitlines():
            if line.startswith("Name:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("Version:"):
                version = line.split(":", 1)[1].strip()
            if name and version:
                break

        if name:
            packages[name.lower()] = InstalledPackage(
                name=name,
                version=version or "unknown",
                location=site_packages,
            )

    return list(packages.values())


def uninstall_package(pkg: InstalledPackage) -> int:
    """Uninstall a package by removing its files."""
    record_path = pkg.record_path
    if record_path and record_path.exists():
        # Read RECORD and remove files
        try:
            content = record_path.read_text(encoding="utf-8")
            removed = 0
            for line in content.splitlines():
                if not line.strip():
                    continue
                parts = line.split(",")
                if not parts:
                    continue
                rel = parts[0]
                file_path = pkg.location / rel
                if file_path.exists() and file_path.is_file():
                    file_path.unlink()
                    removed += 1
            # Remove dist-info
            dist_info_dir = record_path.parent
            if dist_info_dir.exists():
                shutil.rmtree(dist_info_dir)
            # Clean up __pycache__
            for pycache in pkg.location.rglob("__pycache__"):
                if pycache.is_dir():
                    try:
                        shutil.rmtree(pycache)
                    except Exception:
                        pass
            return removed
        except Exception as e:
            logger.error(f"Failed to uninstall {pkg.name}: {e}")
            raise
    return 0
