#!/usr/bin/env python3
# -*- coding: UTF-8 -*-
'''
@File    ：scheduler.py
@Author  ：even_lin
@Date    ：2025/6/18 18:23 
@Desc     : {模块描述}
'''
import asyncio
from contextlib import suppress
from typing import Any, Optional, cast

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.base.logging import logger
from src.base.redis import BaseRedisManager
from src.config import config
from src.jobs.ai_model import SyncAiModelJob
from src.jobs.chat_log import SyncChatLogJob
from src.jobs.except_log import SyncExceptLogJob


class SchedulerManager:
    """
    定时任务调度器 单例
    """

    _instance: Optional["SchedulerManager"] = None
    _scheduler: Optional[AsyncIOScheduler]
    _chat_log_job: Optional[SyncChatLogJob]
    _except_log_job: Optional[SyncExceptLogJob]
    _heartbeat_task: Optional[asyncio.Task]

    def __new__(cls):
        if cls._instance is None:
            instance = super(SchedulerManager, cls).__new__(cls)
            instance._scheduler = None
            instance._chat_log_job = None
            instance._except_log_job = None
            instance._heartbeat_task = None
            logger.info("SchedulerManager initialized.")
            cls._instance = instance
        return cls._instance

    @staticmethod
    def _create_scheduler() -> AsyncIOScheduler:
        return AsyncIOScheduler()

    @staticmethod
    def _redis_client() -> Any:
        redis_client = cast(Any, BaseRedisManager.client)
        if redis_client is None:
            raise RuntimeError("Redis client is not initialized before scheduler startup")
        return redis_client

    @staticmethod
    def _signal_key() -> str:
        return config.SCHEDULER_SIGNAL_KEY

    @staticmethod
    def _signal_value() -> str:
        return f"online:{config.SERVER_NAME}"

    @staticmethod
    def _signal_ttl_seconds() -> int:
        return config.SCHEDULER_SIGNAL_TTL_SECONDS

    @staticmethod
    def _signal_refresh_interval_seconds() -> int:
        return config.SCHEDULER_SIGNAL_REFRESH_INTERVAL_SECONDS

    def _ensure_scheduler(self) -> AsyncIOScheduler:
        if self._scheduler is None:
            self._scheduler = self._create_scheduler()
        return self._scheduler

    async def _publish_scheduler_signal(self) -> None:
        await self._redis_client().set(
            self._signal_key(),
            self._signal_value(),
            ex=self._signal_ttl_seconds(),
        )

    async def _clear_scheduler_signal(self) -> None:
        await self._redis_client().delete(self._signal_key())

    async def _heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._signal_refresh_interval_seconds())
                try:
                    await self._publish_scheduler_signal()
                except Exception as exc:
                    logger.warning(f"Failed to refresh scheduler heartbeat: {exc}")
        except asyncio.CancelledError:
            raise
        finally:
            self._heartbeat_task = None

    async def _cancel_heartbeat_task(self) -> None:
        if self._heartbeat_task is None:
            return
        self._heartbeat_task.cancel()
        with suppress(asyncio.CancelledError):
            await self._heartbeat_task

    def _register_jobs(self):
        """
        注册定时任务
        """
        scheduler = self._ensure_scheduler()

        if config.CHAT_LOG_ENABLE:
            self._chat_log_job = SyncChatLogJob()
            scheduler.add_job(
                self._chat_log_job.task_report_main_queue,
                "interval",
                seconds=config.CHAT_LOG_REPORT_INTERVAL,
                id="job_report_main_queue",
                name="主日志队列上报任务",
                replace_existing=True,
            )
            scheduler.add_job(
                self._chat_log_job.task_retry_dlq,
                "interval",
                seconds=config.CHAT_LOG_RETRY_REPORT_INTERVAL,
                id="job_retry_dlq",
                name="死信队列重试任务",
                replace_existing=True,
            )
            logger.info("Chat log reporter jobs have been registered.")
        else:
            self._chat_log_job = None
            logger.info("Chat log reporter is disabled, skipping chat log jobs registration.")

        if config.EXCEPT_LOG_ENABLE:
            self._except_log_job = SyncExceptLogJob()
            scheduler.add_job(
                self._except_log_job.task_report_queue,
                "interval",
                seconds=config.EXCEPT_LOG_REPORT_INTERVAL,
                id="job_report_except_log",
                name="异常日志队列上报任务",
                replace_existing=True,
            )
            logger.info("Except log reporter jobs have been registered.")
        else:
            self._except_log_job = None
            logger.info("Except log reporter is disabled, skipping except log jobs registration.")

        sync_ai_model_job = SyncAiModelJob()
        scheduler.add_job(
            sync_ai_model_job.sync_ai_model,
            "interval",
            seconds=config.SYNC_AI_MODEL_INTERVAL,
            id="job_sync_ai_model",
            name="从api-serve同步AI模型",
            replace_existing=True,
        )

        logger.info("All APScheduler jobs have been registered.")

    async def _close_job_clients(self) -> None:
        close_tasks = []
        if self._chat_log_job is not None:
            close_tasks.append(self._chat_log_job.close())
        if self._except_log_job is not None:
            close_tasks.append(self._except_log_job.close())

        self._chat_log_job = None
        self._except_log_job = None

        if close_tasks:
            results = await asyncio.gather(*close_tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    logger.warning(f"Failed to close reporter client: {result}")

    async def _stop_scheduler(self) -> None:
        scheduler = self._scheduler
        if scheduler is not None and scheduler.running:
            scheduler.shutdown()
            logger.info("Scheduler shut down.")
        self._scheduler = None
        await self._close_job_clients()

    async def start(self):
        """
        用于注册任务并启动调度器
        """
        scheduler = self._ensure_scheduler()
        if scheduler.running:
            return

        self._register_jobs()
        await SyncAiModelJob().sync_ai_model()
        scheduler.start()
        await self._publish_scheduler_signal()
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info("Scheduler started.")

    async def shutdown(self):
        await self._cancel_heartbeat_task()
        try:
            await self._clear_scheduler_signal()
        except Exception as exc:
            logger.warning(f"Failed to clear scheduler heartbeat: {exc}")
        await self._stop_scheduler()


# 实例化
scheduler_manager = SchedulerManager()
