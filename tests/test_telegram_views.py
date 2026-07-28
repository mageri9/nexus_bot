import pytest

from telegram.callbacks import GlobalStatsCallback, RootMenuCallback
from telegram import views


class EmptyRedis:
    async def scan_iter(self, _pattern):
        if False:
            yield None


@pytest.mark.asyncio
async def test_global_stats_uses_routable_back_callback(monkeypatch):
    async def list_recent_incidents(_limit):
        return []

    monkeypatch.setattr(views, "redis_client", EmptyRedis())
    monkeypatch.setattr(
        views.incident_service, "list_recent_incidents", list_recent_incidents
    )

    _, markup = await views.build_global_stats_content()

    assert markup.inline_keyboard[0][0].callback_data == RootMenuCallback().pack()


@pytest.mark.asyncio
async def test_dashboard_uses_routable_global_stats_callback(monkeypatch):
    async def get_agent_details(_agent_name):
        return {}

    async def get_timeline(_limit):
        return []

    monkeypatch.setattr(
        views.query_service.registry, "list_agents", lambda: []
    )
    monkeypatch.setattr(views.query_service, "get_agent_details", get_agent_details)
    monkeypatch.setattr(views.incident_service, "get_timeline", get_timeline)
    monkeypatch.setattr(views, "get_total_ai_usage", lambda: _usage())

    _, markup = await views.build_dashboard_content()

    assert markup.inline_keyboard[0][0].callback_data == GlobalStatsCallback().pack()


async def _usage():
    return {"prompt": 0, "completion": 0, "total": 0, "requests": 0}
