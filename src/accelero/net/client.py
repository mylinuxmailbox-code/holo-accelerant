"""
High-performance HTTP client for package index operations.

Key optimizations:
- Connection pooling and keep-alive
- HTTP/2 for multiplexed requests
- Async I/O for concurrent operations
- Automatic retries with exponential backoff
- Response caching
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import urljoin, urlparse

import httpx

logger = logging.getLogger("accelero.net")


@dataclass
class HTTPResponse:
    """Wrapper for HTTP response with metadata."""

    status: int
    content: bytes
    headers: dict[str, str]
    url: str
    elapsed: float

    def raise_for_status(self) -> None:
        """Raise an exception for 4xx/5xx responses."""
        if 400 <= self.status < 600:
            import httpx
            raise httpx.HTTPStatusError(
                f"HTTP {self.status}",
                request=None,
                response=None,
            )


@dataclass
class DownloadProgress:
    """Download progress tracking."""

    bytes_downloaded: int = 0
    total_bytes: int | None = None
    speed_bps: float = 0.0
    started_at: float = field(default_factory=time.time)


class HTTPClient:
    """
    High-performance async HTTP client optimized for PyPI operations.

    Features:
    - HTTP/2 connection pooling
    - Automatic retries with backoff
    - Streaming downloads with progress
    - DNS caching
    - TLS session resumption
    """

    DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=5.0)
    MAX_RETRIES = 3
    MAX_CONCURRENT = 32

    def __init__(
        self,
        timeout: httpx.Timeout | None = None,
        max_connections: int = 100,
        max_keepalive: int = 20,
        verify_ssl: bool = True,
        headers: dict[str, str] | None = None,
    ):
        self.timeout = timeout or self.DEFAULT_TIMEOUT
        self.verify_ssl = verify_ssl

        # Default headers
        default_headers = {
            "User-Agent": "holo-accelerant/0.1.0 (Python 3.10)",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        }
        if headers:
            default_headers.update(headers)
        self.default_headers = default_headers

        # Transport configuration
        limits = httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max_keepalive,
        )

        # HTTP/2 is enabled by default for multiplexed requests
        self._client: httpx.AsyncClient | None = None
        self._config = {
            "timeout": self.timeout,
            "limits": limits,
            "verify": verify_ssl,
            "headers": default_headers,
            "http2": True,
        }
        self._request_count = 0
        self._total_bytes_sent = 0
        self._total_bytes_received = 0
        self._total_time = 0.0

    async def __aenter__(self) -> "HTTPClient":
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Lazily create the HTTP client."""
        if self._client is None:
            try:
                self._client = httpx.AsyncClient(**self._config)
            except ImportError:
                if self._config.get("http2"):
                    logger.warning("HTTP/2 support unavailable; falling back to HTTP/1.1")
                    self._config["http2"] = False
                    self._client = httpx.AsyncClient(**self._config)
                else:
                    raise
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def get(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        follow_redirects: bool = True,
        timeout: httpx.Timeout | None = None,
    ) -> HTTPResponse:
        """Perform a GET request."""
        client = await self._ensure_client()
        merged_headers = dict(self.default_headers)
        if headers:
            merged_headers.update(headers)

        effective_timeout = timeout or self.timeout
        start = time.perf_counter()

        for attempt in range(self.MAX_RETRIES):
            try:
                response = await client.get(
                    url,
                    headers=merged_headers,
                    params=params,
                    follow_redirects=follow_redirects,
                    timeout=effective_timeout,
                )
                elapsed = time.perf_counter() - start
                self._request_count += 1
                self._total_bytes_received += len(response.content)
                self._total_time += elapsed

                return HTTPResponse(
                    status=response.status_code,
                    content=response.content,
                    headers=dict(response.headers),
                    url=str(response.url),
                    elapsed=elapsed,
                )
            except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as e:
                if attempt == self.MAX_RETRIES - 1:
                    raise
                wait = 2**attempt * 0.1
                logger.debug(f"Request failed, retrying in {wait:.1f}s: {url}")
                await asyncio.sleep(wait)

        raise RuntimeError("Unreachable")

    async def download_stream(
        self,
        url: str,
        dest: Path,
        progress: DownloadProgress | None = None,
        chunk_size: int = 65536,
    ) -> tuple[Path, str]:
        """
        Stream download a file to disk with progress tracking.

        Returns (path, content_hash).
        """
        client = await self._ensure_client()
        merged_headers = dict(self.default_headers)
        # Avoid compression for binary downloads
        merged_headers.pop("Accept-Encoding", None)

        progress = progress or DownloadProgress()
        hasher = hashlib.sha256()

        with open(dest, "wb") as f:
            async with client.stream(
                "GET",
                url,
                headers=merged_headers,
                timeout=self.timeout,
            ) as response:
                response.raise_for_status()
                content_length = response.headers.get("content-length")
                if content_length:
                    progress.total_bytes = int(content_length)

                async for chunk in response.aiter_bytes(chunk_size=chunk_size):
                    f.write(chunk)
                    hasher.update(chunk)
                    progress.bytes_downloaded += len(chunk)
                    elapsed = time.time() - progress.started_at
                    if elapsed > 0:
                        progress.speed_bps = progress.bytes_downloaded / elapsed

        return dest, hasher.hexdigest()

    async def download_batch(
        self,
        urls: list[tuple[str, Path]],
        max_concurrent: int = MAX_CONCURRENT,
        progress_callback=None,
    ) -> dict[str, tuple[Path, str]]:
        """
        Download multiple files concurrently.

        Args:
            urls: List of (url, destination_path) tuples
            max_concurrent: Maximum concurrent downloads
            progress_callback: Optional callback(downloaded, total, url)

        Returns:
            Dict mapping url -> (path, content_hash)
        """
        semaphore = asyncio.Semaphore(max_concurrent)
        results: dict[str, tuple[Path, str]] = {}

        async def download_one(url: str, dest: Path) -> tuple[str, Path, str]:
            async with semaphore:
                try:
                    path, hash_val = await self.download_stream(url, dest)
                    results[url] = (path, hash_val)
                    if progress_callback:
                        progress_callback(len(results), len(urls), url)
                    return url, path, hash_val
                except Exception as e:
                    logger.error(f"Download failed: {url}: {e}")
                    raise

        tasks = [download_one(url, dest) for url, dest in urls]
        await asyncio.gather(*tasks, return_exceptions=True)
        return results

    def get_stats(self) -> dict[str, Any]:
        """Return HTTP client statistics."""
        return {
            "requests": self._request_count,
            "bytes_sent": self._total_bytes_sent,
            "bytes_received": self._total_bytes_received,
            "total_time": self._total_time,
        }


class IndexClient:
    """
    Client for PyPI/simple API interactions.

    Supports:
    - PEP 503 Simple Repository API
    - PEP 691 JSON API
    """

    def __init__(self, http_client: HTTPClient, base_url: str = "https://pypi.org/"):
        self.http = http_client
        self.base_url = base_url.rstrip("/")
        self._cache: dict[str, Any] = {}

    async def get_package_index(self, package_name: str) -> dict[str, Any]:
        """
        Get package metadata from the index.

        Uses JSON API (PEP 691) for efficiency.
        """
        cache_key = f"{self.base_url}/{package_name}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        url = f"{self.base_url}/pypi/{package_name}/json"
        resp = await self.http.get(url)

        if resp.status == 404:
            raise PackageNotFound(f"Package not found: {package_name}")

        resp.raise_for_status()
        import json

        data = json.loads(resp.content)
        self._cache[cache_key] = data
        return data

    async def get_simple_index(self) -> dict[str, list[str]]:
        """
        Get the simple package index (PEP 503).

        Returns dict mapping package_name -> list of file URLs.
        """
        cache_key = f"{self.base_url}/simple"
        if cache_key in self._cache:
            return self._cache[cache_key]

        url = f"{self.base_url}/simple/"
        resp = await self.http.get(url, headers={"Accept": "text/html"})

        # Parse HTML index
        from html.parser import HTMLParser

        class SimpleIndexParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.packages: dict[str, list[str]] = {}
                self.current_a: str | None = None
                self.in_a = False

            def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
                if tag == "a":
                    for name, value in attrs:
                        if name == "href" and value:
                            self.current_a = value
                            self.in_a = True
                            break

            def handle_endtag(self, tag: str) -> None:
                if tag == "a":
                    self.in_a = False
                    self.current_a = None

            def handle_data(self, data: str) -> None:
                if self.in_a and self.current_a:
                    name = data.strip().lower()
                    if name not in self.packages:
                        self.packages[name] = []
                    if self.current_a not in self.packages[name]:
                        self.packages[name].append(self.current_a)

        parser = SimpleIndexParser()
        parser.feed(resp.content.decode("utf-8", errors="replace"))

        self._cache[cache_key] = parser.packages
        return parser.packages

    def clear_cache(self) -> None:
        """Clear the index cache."""
        self._cache.clear()


class PackageNotFound(Exception):
    """Raised when a package cannot be found."""
