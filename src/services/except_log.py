#!/usr/bin/env python3
# -*- coding: UTF-8 -*-
'''
@File    ：except_log.py
@Author  ：even_lin
@Date    ：2026/01/07
@Desc     : 异常日志上报服务
'''
import base64
import hashlib
import json
import time

import httpx

from src.base.logging import logger
from src.config import config
from src.dao.redis.managers.except_log import ExceptLogRedisManager
from src.services.chat_log import ChatLogReporter


class ExceptLogReporter:
    def __init__(self):
        self.report_url = config.EXCEPT_LOG_REPORT_URL
        self.batch_size = config.EXCEPT_LOG_BATCH_SIZE
        self.client = httpx.AsyncClient(timeout=30.0)

    async def _send_logs(self, logs_str_list: list[str]) -> bool:
        """
        发送日志的通用内部方法
        返回 True 表示成功, False 表示失败
        """
        if not logs_str_list or len(logs_str_list) == 0:
            logger.info("ExceptLogReporter No except logs to report.")
            return True  # 没有日志也算成功处理

        logs_data = []
        for log_str in logs_str_list:
            try:
                logs_data_single = json.loads(log_str)
                if isinstance(logs_data_single, dict):
                    logs_data.append(logs_data_single)
            except json.JSONDecodeError:
                logger.error(f"ExceptLogReporter Invalid except log format, not a valid JSON: {log_str}")
                continue
        
        if not logs_data:
            logger.info("ExceptLogReporter No valid except logs to report after processing.")
            return True
        
        payload = {"items": logs_data}

        try:
            logger.info(f"ExceptLogReporter Reporting {len(logs_data)} except logs to {self.report_url}")
            auth_headers = ChatLogReporter.generate_auth_headers("post")
            response = await self.client.post(self.report_url, json=payload, headers=auth_headers)
            logger.debug(f"ExceptLogReporter Response: {response.json()}")

            response.raise_for_status()  # 状态码为4xx或5xx时抛出异常
            logger.info(f"ExceptLogReporter Successfully reported {len(logs_data)} except logs. Status: {response.status_code}")
            return True
        except httpx.HTTPStatusError as e:
            logger.error(f"ExceptLogReporter Failed to report except logs, HTTP error: {e.response.status_code}, response: {e.response.text}")
            return False
        except Exception as e:
            logger.error(f"ExceptLogReporter An unexpected error occurred during except log reporting: {e}", exc_info=True)
            return False

    async def report_from_queue(self):
        """
        从队列获取并上报异常日志
        """
        logger.info("ExceptLogReporter Reporting except logs from queue")
        logs_str_list_report = await ExceptLogRedisManager.get_except_logs_batch(self.batch_size)
        if not logs_str_list_report:
            logger.info("ExceptLogReporter Except log queue is empty.")
            return

        success = await self._send_logs(logs_str_list_report)

        if not success:
            # 上报失败，直接丢弃并记录错误日志
            logger.error(f"ExceptLogReporter Failed to report {len(logs_str_list_report)} except logs. Logs discarded.")

    async def close(self):
        await self.client.aclose()
