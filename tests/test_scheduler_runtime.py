import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.base.redis import BaseRedisManager
from src.config import config
from src.jobs.ai_model import SyncAiModelJob
from src.jobs.scheduler import SchedulerManager


class FakeScheduler:
    def __init__(self):
        self.running = False

    def start(self):
        self.running = True

    def shutdown(self):
        self.running = False


class FakeRedis:
    def __init__(self):
        self.store: dict[str, dict[str, str | int]] = {}

    async def set(self, key: str, value: str, ex: int | None = None):
        self.store[key] = {"value": value, "ttl": ex or 0}
        return True

    async def delete(self, key: str):
        existed = key in self.store
        self.store.pop(key, None)
        return 1 if existed else 0


def _build_manager() -> SchedulerManager:
    SchedulerManager._instance = None
    return SchedulerManager()


def test_scheduler_start_registers_jobs_runs_initial_sync_and_publishes_signal():
    async def scenario():
        fake_redis = FakeRedis()
        with patch.object(BaseRedisManager, "client", fake_redis), patch.object(
            config, "SCHEDULER_SIGNAL_KEY", "test:scheduler:online", create=True
        ), patch.object(config, "SCHEDULER_SIGNAL_TTL_SECONDS", 30, create=True), patch.object(
            config, "SCHEDULER_SIGNAL_REFRESH_INTERVAL_SECONDS", 10, create=True
        ), patch.object(
            SchedulerManager, "_create_scheduler", new=staticmethod(FakeScheduler)
        ), patch.object(
            SchedulerManager, "_register_jobs", new=lambda self: None
        ), patch.object(SyncAiModelJob, "sync_ai_model", new_callable=AsyncMock) as sync_mock:
            manager = _build_manager()

            await manager.start()
            await manager.start()

            assert manager._scheduler is not None
            assert manager._scheduler.running is True
            assert sync_mock.await_count == 1
            assert fake_redis.store[config.SCHEDULER_SIGNAL_KEY]["ttl"] == 30
            assert str(fake_redis.store[config.SCHEDULER_SIGNAL_KEY]["value"]).startswith("online:")

            await manager.shutdown()
            assert config.SCHEDULER_SIGNAL_KEY not in fake_redis.store
            SchedulerManager._instance = None

    asyncio.run(scenario())


def test_scheduler_shutdown_stops_running_scheduler_and_cleans_up():
    async def scenario():
        fake_redis = FakeRedis()
        close_mock = AsyncMock()
        with patch.object(BaseRedisManager, "client", fake_redis), patch.object(
            config, "SCHEDULER_SIGNAL_KEY", "test:scheduler:online", create=True
        ), patch.object(config, "SCHEDULER_SIGNAL_TTL_SECONDS", 30, create=True), patch.object(
            config, "SCHEDULER_SIGNAL_REFRESH_INTERVAL_SECONDS", 10, create=True
        ), patch.object(
            SchedulerManager, "_create_scheduler", new=staticmethod(FakeScheduler)
        ), patch.object(
            SchedulerManager, "_register_jobs", new=lambda self: None
        ), patch.object(SyncAiModelJob, "sync_ai_model", new_callable=AsyncMock), patch(
            "src.jobs.scheduler.SyncChatLogJob"
        ) as chat_job_cls:
            chat_job_cls.return_value.close = close_mock
            manager = _build_manager()
            manager._chat_log_job = chat_job_cls.return_value

            await manager.start()
            await manager.shutdown()

            assert manager._scheduler is None
            close_mock.assert_awaited_once()
            assert config.SCHEDULER_SIGNAL_KEY not in fake_redis.store
            SchedulerManager._instance = None

    asyncio.run(scenario())


def test_scheduler_shutdown_before_start_is_safe():
    async def scenario():
        fake_redis = FakeRedis()
        with patch.object(BaseRedisManager, "client", fake_redis), patch.object(
            config, "SCHEDULER_SIGNAL_KEY", "test:scheduler:online", create=True
        ):
            manager = _build_manager()
            await manager.shutdown()
            assert manager._scheduler is None
            assert fake_redis.store == {}
            SchedulerManager._instance = None

    asyncio.run(scenario())
