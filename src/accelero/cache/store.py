"""
High-performance local cache for package metadata and downloads.

Architecture:
- Content-addressable storage for wheels/sdists
- SQLite for metadata and index data
- LRU eviction
- Atomic writes with temp file + rename
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterator

from accelero.utils.paths import get_cache_dir

logger = logging.getLogger("accelero.cache")


class Cache:
    """
    High-performance package cache with content-addressable storage.

    Layout:
        cache_dir/
        ├── wheels/         # Content-addressable wheel storage
        ├── sdists/         # Content-addressable sdist storage
        ├── metadata/       # Package metadata (JSON files)
        ├── index/          # Cached index data
        ├── cache.db        # SQLite metadata database
        └── info.json       # Cache metadata
    """

    def __init__(self, cache_dir: Path | None = None):
        self.cache_dir = cache_dir or get_cache_dir()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Subdirectories
        self.wheels_dir = self.cache_dir / "wheels"
        self.sdists_dir = self.cache_dir / "sdists"
        self.metadata_dir = self.cache_dir / "metadata"
        self.index_dir = self.cache_dir / "index"
        self.db_path = self.cache_dir / "cache.db"

        for d in (self.wheels_dir, self.sdists_dir, self.metadata_dir, self.index_dir):
            d.mkdir(exist_ok=True)

        self._init_db()
        self.hits = 0
        self.misses = 0

    def _init_db(self) -> None:
        """Initialize the cache database."""
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value BLOB,
                    etag TEXT,
                    last_modified TEXT,
                    expires_at REAL,
                    created_at REAL
                );

                CREATE TABLE IF NOT EXISTS wheels (
                    sha256 TEXT PRIMARY KEY,
                    package_name TEXT,
                    version TEXT,
                    filename TEXT,
                    size INTEGER,
                    url TEXT,
                    created_at REAL
                );

                CREATE TABLE IF NOT EXISTS index_entries (
                    package_name TEXT PRIMARY KEY,
                    data BLOB,
                    expires_at REAL,
                    created_at REAL
                );

                CREATE INDEX IF NOT EXISTS idx_metadata_expires
                    ON metadata(expires_at);
                CREATE INDEX IF NOT EXISTS idx_index_expires
                    ON index_entries(expires_at);
            """)
            conn.commit()

    @contextmanager
    def _get_conn(self) -> Iterator[sqlite3.Connection]:
        """Get a database connection with row factory."""
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=10.0,
            isolation_level=None,  # autocommit mode
        )
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # === Metadata caching ===

    def get_metadata(self, key: str) -> dict[str, Any] | None:
        """Get cached metadata, respecting TTL."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT value, expires_at FROM metadata WHERE key = ?",
                (key,),
            ).fetchone()
            if row is None:
                self.misses += 1
                return None
            if row["expires_at"] and row["expires_at"] < time.time():
                self.misses += 1
                return None
            self.hits += 1
            return json.loads(row["value"])

    def set_metadata(
        self,
        key: str,
        value: dict[str, Any],
        ttl: float = 600.0,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> None:
        """Cache metadata with TTL."""
        data = json.dumps(value).encode("utf-8")
        expires = time.time() + ttl
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO metadata
                    (key, value, etag, last_modified, expires_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (key, data, etag, last_modified, expires, time.time()),
            )

    # === Index caching ===

    def get_index(self, package_name: str) -> dict[str, Any] | None:
        """Get cached package index data."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT data, expires_at FROM index_entries WHERE package_name = ?",
                (package_name.lower(),),
            ).fetchone()
            if row is None:
                self.misses += 1
                return None
            if row["expires_at"] and row["expires_at"] < time.time():
                self.misses += 1
                return None
            self.hits += 1
            return json.loads(row["data"])

    def set_index(
        self,
        package_name: str,
        data: dict[str, Any],
        ttl: float = 3600.0,
    ) -> None:
        """Cache package index data."""
        payload = json.dumps(data).encode("utf-8")
        expires = time.time() + ttl
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO index_entries
                    (package_name, data, expires_at, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (package_name.lower(), payload, expires, time.time()),
            )

    # === Content-addressable file storage ===

    def _content_path(self, base: Path, sha256: str) -> Path:
        """Return the path for a content hash (sharded by prefix)."""
        return base / sha256[:2] / sha256[2:4] / sha256

    def has_content(self, sha256: str, kind: str = "wheels") -> bool:
        """Check if content is cached."""
        base = self.wheels_dir if kind == "wheels" else self.sdists_dir
        return self._content_path(base, sha256).exists()

    def get_content_path(self, sha256: str, kind: str = "wheels") -> Path:
        """Get the path where content is (or will be) stored."""
        base = self.wheels_dir if kind == "wheels" else self.sdists_dir
        return self._content_path(base, sha256)

    def store_wheel(
        self,
        source: Path,
        package_name: str,
        version: str,
        filename: str,
        url: str,
    ) -> str:
        """
        Atomically store a wheel file in the cache.

        Returns the SHA256 hash of the file.
        """
        # Compute hash while copying
        hasher = hashlib.sha256()
        size = 0
        with open(source, "rb") as src:
            while True:
                chunk = src.read(65536)
                if not chunk:
                    break
                hasher.update(chunk)
                size += len(chunk)
        sha256 = hasher.hexdigest()

        dest = self.get_content_path(sha256, "wheels")
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Atomic copy via temp + rename
            temp_dest = dest.with_suffix(".tmp")
            import shutil

            shutil.copy2(source, temp_dest)
            os.replace(temp_dest, dest)

        # Record in database
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO wheels
                    (sha256, package_name, version, filename, size, url, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (sha256, package_name, version, filename, size, url, time.time()),
            )

        return sha256

    def find_wheel(self, package_name: str, version: str, filename: str) -> Path | None:
        """Find a cached wheel by package+version+filename."""
        with self._get_conn() as conn:
            row = conn.execute(
                """
                SELECT sha256 FROM wheels
                WHERE package_name = ? AND version = ? AND filename = ?
                """,
                (package_name, version, filename),
            ).fetchone()
            if row is None:
                return None
            path = self.get_content_path(row["sha256"], "wheels")
            if path.exists():
                self.hits += 1
                return path
            return None

    # === Statistics ===

    def stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        with self._get_conn() as conn:
            wheel_count = conn.execute("SELECT COUNT(*) AS c FROM wheels").fetchone()["c"]
            total_size = conn.execute("SELECT COALESCE(SUM(size), 0) AS s FROM wheels").fetchone()["s"]
            metadata_count = conn.execute("SELECT COUNT(*) AS c FROM metadata").fetchone()["c"]
            index_count = conn.execute("SELECT COUNT(*) AS c FROM index_entries").fetchone()["c"]

        # Disk usage
        disk_size = 0
        for d in (self.wheels_dir, self.sdists_dir, self.metadata_dir):
            for path in d.rglob("*"):
                if path.is_file():
                    disk_size += path.stat().st_size

        return {
            "cache_dir": str(self.cache_dir),
            "wheels_cached": wheel_count,
            "total_size_bytes": total_size,
            "disk_usage_bytes": disk_size,
            "metadata_entries": metadata_count,
            "index_entries": index_count,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": (
                self.hits / (self.hits + self.misses)
                if (self.hits + self.misses) > 0
                else 0
            ),
        }

    def clean(self) -> dict[str, int]:
        """Remove all cached content."""
        removed = 0
        freed_bytes = 0

        for d in (self.wheels_dir, self.sdists_dir, self.metadata_dir):
            if d.exists():
                for path in d.rglob("*"):
                    if path.is_file():
                        freed_bytes += path.stat().st_size
                        path.unlink()
                        removed += 1
            # Remove empty directories
            for path in sorted(d.rglob("*"), reverse=True):
                if path.is_dir():
                    try:
                        path.rmdir()
                    except OSError:
                        pass

        with self._get_conn() as conn:
            conn.execute("DELETE FROM metadata")
            conn.execute("DELETE FROM index_entries")
            conn.execute("DELETE FROM wheels")

        return {"files_removed": removed, "bytes_freed": freed_bytes}

    def prune(self, max_age_days: int = 30) -> dict[str, int]:
        """Remove cache entries older than max_age_days."""
        cutoff = time.time() - (max_age_days * 86400)
        removed = 0
        freed_bytes = 0

        with self._get_conn() as conn:
            # Get wheel entries to remove
            rows = conn.execute(
                "SELECT sha256, size FROM wheels WHERE created_at < ?",
                (cutoff,),
            ).fetchall()

            for row in rows:
                path = self.get_content_path(row["sha256"], "wheels")
                if path.exists():
                    freed_bytes += path.stat().st_size
                    path.unlink()
                    removed += 1

            conn.execute("DELETE FROM wheels WHERE created_at < ?", (cutoff,))
            conn.execute("DELETE FROM metadata WHERE created_at < ?", (cutoff,))
            conn.execute("DELETE FROM index_entries WHERE created_at < ?", (cutoff,))

        return {"files_removed": removed, "bytes_freed": freed_bytes}
