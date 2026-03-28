#!/usr/bin/env python3
# -*- coding: UTF-8 -*-
'''
@File    ：chat_log.py
@Author  ：even_lin
@Date    ：2025/6/10 13:55
@Desc     : {模块描述}
'''

from src.base.logging import logger
from src.services.chat_log import ChatLogReporter

class SyncChatLogJob:
    """
    同步chat日志相关定时任务
    """
    def __init__(self):
        self.reporter = ChatLogReporter()
        logger.info("ChatLogReporter instance created for SyncChatLogJob.")

    async def task_report_main_queue(self):
        """
        定时从主队列上报日志.
        """
        await self.reporter.report_from_main_queue()

    async def task_retry_dlq(self):
        """
        定时从死信队列重试上报失败的日志.
        """
        await self.reporter.retry_from_dead_letter_queue()

    async def close(self):
        await self.reporter.close()
