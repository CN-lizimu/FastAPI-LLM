import pytest

from routers import health as health_router


class FakeDB:
    async def execute(self, statement):
        return 1


@pytest.mark.asyncio
async def test_health_check_ok(monkeypatch):
    async def fake_ping():
        return True

    monkeypatch.setattr(health_router.redis_client, "ping", fake_ping)
    response = await health_router.health_check(FakeDB())
    assert response.status_code == 200
    assert b'"database":"ok"' in response.body
    assert b'"redis":"ok"' in response.body


@pytest.mark.asyncio
async def test_health_check_degraded_when_redis_is_down(monkeypatch):
    async def fake_ping():
        raise ConnectionError("redis unavailable")

    monkeypatch.setattr(health_router.redis_client, "ping", fake_ping)
    response = await health_router.health_check(FakeDB())
    assert response.status_code == 503
    assert b'"redis":"error"' in response.body
