from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, TypeVar

from django.conf import settings

T = TypeVar("T")

_search_semaphore: asyncio.Semaphore | None = None


def get_search_semaphore() -> asyncio.Semaphore:
    global _search_semaphore
    if _search_semaphore is None:
        limit = int(getattr(settings, "CHAT_MAX_CONCURRENT_SEARCHES", 8))
        _search_semaphore = asyncio.Semaphore(max(1, limit))
    return _search_semaphore


async def run_with_search_limit(func: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Run a blocking Knowledge search tool without exceeding concurrent search slots."""
    async with get_search_semaphore():
        return await asyncio.to_thread(func, *args, **kwargs)
