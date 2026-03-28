#!/usr/bin/env python3
# -*- coding: UTF-8 -*-
'''
@File    ：except_log.py
@Author  ：even_lin
@Date    ：2026/01/07
@Desc     : 异常日志上报定时任务
'''

from src.base.logging import logger
from src.services.except_log import ExceptLogReporter


class SyncExceptLogJob:
    """
    同步异常日志相关定时任务
    """
    def __init__(self):
        self.reporter = ExceptLogReporter()
        logger.info("ExceptLogReporter instance created for SyncExceptLogJob.")

    async def task_report_queue(self):
        """
        定时从队列上报异常日志.
        """
        await self.reporter.report_from_queue()

    async def close(self):
        await self.reporter.close()
