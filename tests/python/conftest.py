"""Shared fixtures: JSON fixture loading and a fake browser stack (no network, no real browser)."""

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def sleep(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Retries must never really wait; tests can assert on the requested delays."""
    mock = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", mock)
    return mock


@dataclass
class Reply:
    """What the fake network answers for one navigation."""

    status: int = 200
    html: str = "<html><body><p>ok</p></body></html>"
    headers: dict[str, str] = field(default_factory=dict)
    text: str = ""


class FakeWeb:
    """A fake ``BrowserManager`` whose pages answer from ``routes``.

    ``routes`` maps URL to a ``Reply``, an exception to raise from ``page.goto`` or a list of
    those, consumed one per navigation (the last entry repeats). Unknown URLs answer 404.
    """

    def __init__(self, routes: dict[str, Any] | None = None) -> None:
        self.routes: dict[str, Any] = dict(routes or {})
        self.visited: list[str] = []
        self.contexts: list[Mock] = []
        self.pages: list[Mock] = []
        self.new_page_error: BaseException | None = None
        self.browser = Mock(
            is_connected=Mock(return_value=True),
            new_context=AsyncMock(side_effect=self._new_context),
        )
        self.manager = Mock(get=AsyncMock(return_value=self.browser), close=AsyncMock())

    def _next(self, url: str) -> Any:
        entry = self.routes.get(url, Reply(status=404))
        if isinstance(entry, list):
            return entry.pop(0) if len(entry) > 1 else entry[0]
        return entry

    async def _new_context(self) -> Mock:
        async def new_page() -> Mock:
            if self.new_page_error:
                raise self.new_page_error
            return self._new_page()

        context = Mock(add_cookies=AsyncMock(), new_page=AsyncMock(side_effect=new_page))
        context.close = AsyncMock()
        self.contexts.append(context)
        return context

    def _new_page(self) -> Mock:
        current: dict[str, Reply] = {}

        async def goto(url: str, **_kwargs: Any) -> Mock:
            self.visited.append(url)
            entry = self._next(url)
            if isinstance(entry, BaseException):
                raise entry
            current["reply"] = entry
            return Mock(
                status=entry.status,
                headers=entry.headers,
                text=AsyncMock(return_value=entry.text),
            )

        async def content() -> str:
            return current["reply"].html

        page = Mock(
            route=AsyncMock(),
            goto=AsyncMock(side_effect=goto),
            wait_for_load_state=AsyncMock(),
            content=AsyncMock(side_effect=content),
        )
        self.pages.append(page)
        return page


@pytest.fixture
def web() -> FakeWeb:
    return FakeWeb()
