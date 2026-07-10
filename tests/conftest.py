import sys
import os
import asyncio
from typing import Callable, Dict, List, Tuple

import pytest
import fakeredis

# Делаем корень репозитория импортируемым (core/, infra/, services/, transports/ и т.д.)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from transports.base import Transport
from core import AgentRegistry, ProjectAgent
from services.event_bus import EventBus


@pytest.fixture
def fake_redis():
    """
    Изолированный in-memory Redis (fakeredis, asyncio-совместимый).
    Каждый тест получает свежий, независимый инстанс — без утечек состояния между тестами.
    """
    client = fakeredis.FakeAsyncRedis(decode_responses=True)
    yield client


class ScriptedTransport(Transport):
    """
    Транспорт, который отвечает по заранее заданному сценарию.

    Использование:
        t = ScriptedTransport()
        t.on(["docker", "inspect", ...], "running")   # сопоставление по точному списку аргументов
        t.on_prefix(["docker", "stats"], "12.5%|30.0%|100MiB / 512MiB")  # сопоставление по префиксу
        t.fail(["docker", "restart", "x"], RuntimeError("boom"))
    """

    def __init__(self):
        self._exact: Dict[Tuple[str, ...], object] = {}
        self._prefix: List[Tuple[Tuple[str, ...], object]] = []
        self.calls: List[List[str]] = []

    def on(self, cmd: List[str], response: str):
        self._exact[tuple(cmd)] = response
        return self

    def on_prefix(self, cmd_prefix: List[str], response: str):
        self._prefix.append((tuple(cmd_prefix), response))
        return self

    def fail(self, cmd: List[str], exc: Exception):
        self._exact[tuple(cmd)] = exc
        return self

    def fail_prefix(self, cmd_prefix: List[str], exc: Exception):
        self._prefix.append((tuple(cmd_prefix), exc))
        return self

    async def run(self, cmd: List[str]) -> str:
        self.calls.append(cmd)
        key = tuple(cmd)

        if key in self._exact:
            result = self._exact[key]
        else:
            result = None
            for prefix, resp in self._prefix:
                if key[: len(prefix)] == prefix:
                    result = resp
                    break
            if result is None:
                raise RuntimeError(f"ScriptedTransport: no script for command {cmd}")

        if isinstance(result, Exception):
            raise result

        # Реальные транспорты (см. transports/local_shell.py) отдают уже
        # .strip()-нутый stdout — повторяем этот контракт здесь, чтобы
        # тесты были написаны против той же семантики, что и в проде.
        return result.strip() if isinstance(result, str) else result


@pytest.fixture
def scripted_transport():
    return ScriptedTransport()


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def registry():
    return AgentRegistry()


class RecordingSubscriber:
    """Подписчик EventBus, который просто запоминает все полученные события."""

    def __init__(self):
        self.received: List[Tuple[str, object]] = []

    async def __call__(self, event_type: str, data: object) -> None:
        self.received.append((event_type, data))

    def types(self) -> List[str]:
        return [t for t, _ in self.received]


@pytest.fixture
def recording_subscriber():
    return RecordingSubscriber()
