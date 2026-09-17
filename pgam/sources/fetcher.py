from __future__ import annotations

import asyncio
import hashlib
import random
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

USER_AGENT = "PostGraduateAdmissionMonitor/0.1 (+personal announcement monitoring)"
MAX_RESPONSE_BYTES = 10 * 1024 * 1024


class FetchError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass(slots=True)
class FetchRequest:
    task_id: int
    url: str
    encoding_override: str | None = None
    allow_insecure_tls: bool = False


@dataclass(slots=True)
class FetchResult:
    task_id: int
    url: str
    final_url: str
    http_status: int
    content_type: str
    encoding: str
    raw_content: bytes
    fetched_at: datetime

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.raw_content).hexdigest()


class Fetcher:
    def __init__(
        self,
        *,
        timeout: float = 20,
        same_host_interval: float = 5,
        max_concurrency: int = 2,
        retries: int = 2,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.timeout = timeout
        self.same_host_interval = same_host_interval
        self.retries = retries
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(follow_redirects=True, timeout=timeout)
        self._global_semaphore = asyncio.Semaphore(max_concurrency)
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._host_last: dict[str, float] = {}
        self._lock_guard = asyncio.Lock()

    async def fetch(self, request: FetchRequest) -> FetchResult:
        host = request.url.split("//", 1)[-1].split("/", 1)[0].lower()
        host_lock = await self._get_host_lock(host)
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            async with self._global_semaphore:
                async with host_lock:
                    await self._respect_host_interval(host)
                    try:
                        return await self._request_once(request)
                    except httpx.HTTPError as exc:
                        last_error = exc
                        retryable = True
                    except FetchError as exc:
                        last_error = exc
                        retryable = exc.status is not None and exc.status >= 500
                        if not retryable:
                            raise
            if attempt >= self.retries:
                raise FetchError(f"Request failed: {last_error}") from last_error
            await asyncio.sleep((2**attempt) * 0.5 + random.random() * 0.2)
        raise FetchError(f"Request failed: {last_error}")

    async def _request_once(self, request: FetchRequest) -> FetchResult:
        temporary_client: httpx.AsyncClient | None = None
        if request.allow_insecure_tls:
            temporary_client = httpx.AsyncClient(
                follow_redirects=True, timeout=self.timeout, verify=False
            )
            client = temporary_client
        else:
            client = self._client
        try:
            response = await client.get(
                request.url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/rss+xml,application/xml;q=0.9,*/*;q=0.8",
                },
            )
            if response.status_code >= 500:
                raise FetchError(f"HTTP {response.status_code}", response.status_code)
            if response.status_code >= 400:
                raise FetchError(f"HTTP {response.status_code}", response.status_code)
            content = response.content
            if len(content) > MAX_RESPONSE_BYTES:
                raise FetchError("Response exceeds the 10 MB limit")
            return FetchResult(
                task_id=request.task_id,
                url=request.url,
                final_url=str(response.url),
                http_status=response.status_code,
                content_type=response.headers.get("content-type", ""),
                encoding=response.encoding or "",
                raw_content=content,
                fetched_at=datetime.now(UTC),
            )
        finally:
            if temporary_client is not None:
                await temporary_client.aclose()

    async def _respect_host_interval(self, host: str) -> None:
        last = self._host_last.get(host, 0)
        wait = self.same_host_interval - (time.monotonic() - last)
        if wait > 0:
            await asyncio.sleep(wait)
        self._host_last[host] = time.monotonic()

    async def _get_host_lock(self, host: str) -> asyncio.Lock:
        async with self._lock_guard:
            return self._host_locks.setdefault(host, asyncio.Lock())

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
