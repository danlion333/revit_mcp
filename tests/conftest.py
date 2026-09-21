"""Test fixtures. See tests/fakebridge.py for what the fake bridge does and does not prove."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest

from revit_mcp.client import RevitClient, Settings
from tests.fakebridge import FakeBridge, Handler


@pytest.fixture
def fake_bridge() -> Iterator[Callable[[Handler], FakeBridge]]:
    bridges: list[FakeBridge] = []

    def start(handler: Handler) -> FakeBridge:
        b = FakeBridge(handler)
        bridges.append(b)
        return b

    yield start
    for b in bridges:
        b.close()


@pytest.fixture
def client_for() -> Callable[[FakeBridge, float], RevitClient]:
    def make(b: FakeBridge, timeout: float = 5.0) -> RevitClient:
        return RevitClient(Settings(host="127.0.0.1", port=b.port, timeout=timeout))

    return make
