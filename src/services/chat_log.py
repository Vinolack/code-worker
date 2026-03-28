#!/usr/bin/env python3
# -*- coding: UTF-8 -*-
'''
@File    ：chat_app.py
@Author  ：even_lin
@Date    ：2025/6/10 13:55
@Desc     : {模块描述}
'''
import base64
import hashlib
import json
import time

import httpx

from src.base.logging import logger
from src.config import config
from src.dao.redis.managers.chat_log import ChatLogRedisManager
from src.utils.api_key_digest_util import ApiKeyDigestUtil

class ChatLogReporter:
    def __init__(self):
        self.report_url = config.CHAT_LOG_REPORT_URL
        self.interval = config.CHAT_LOG_REPORT_INTERVAL
        self.batch_size = config.CHAT_LOG_BATCH_SIZE
        # 新增DLQ重试间隔
        self.dlq_retry_interval = config.CHAT_LOG_RETRY_REPORT_INTERVAL
        self.dlq_retry_batch_size = config.CHAT_LOG_RETRY_BATCH_SIZE
        self.client = httpx.AsyncClient(timeout=30.0)
        # 状态检测的特殊密钥
        self.magic_secret_digest = ApiKeyDigestUtil.create_short_key_digest(config.SERVICE_CHECK_MAGIC_SECRET) if config.SERVICE_CHECK_MAGIC_SECRET and len(config.SERVICE_CHECK_MAGIC_SECRET) > 0 else None
    @staticmethod
    def generate_auth_headers(request_type:str) -> dict:
        """
        生成服务间调用动态身份验证请求头
        """
        secret_key = config.INTERNAL_SERVICE_SECRET_KEY
        timestamp = str(int(time.time()))
        data_to_hash = f"{timestamp}{secret_key}"

        binary_hash = hashlib.sha256(data_to_hash.encode('utf-8')).digest()
        token = base64.urlsafe_b64encode(binary_hash).decode('utf-8').rstrip('=')
        logger.debug(f"Generated auth headers, Authorization:{token},X-Timestamp:{timestamp}")
        if request_type == "post":
            return {
                "X-Timestamp": timestamp,
                "Authorization": token,
                "Content-Type": "application/json",
            }
        if request_type == "get":
            return {
                "X-Timestamp": timestamp,
                "Authorization": token,
            }
        return {
            "X-Timestamp": timestamp,
            "Authorization": token,
            "Content-Type": "application/json",
        }
    async def _send_logs(self, logs_str_list: list[str]) -> bool:
        """
        发送日志的通用内部方法
        返回 True 表示成功, False 表示失败
        """
        if not logs_str_list or len(logs_str_list) == 0:
            logger.info("No logs to report.")
            return True  # 没有日志也算成功处理

        logs_data = []
        for log_str in logs_str_list:
            try:
                logs_data_single = json.loads(log_str)
                if isinstance(logs_data_single, dict):
                    if "api_key" in logs_data_single:
                        # 检测特殊密钥，跳过上报
                        if self.magic_secret_digest is not None and logs_data_single["api_key"] == self.magic_secret_digest:
                            logger.debug("Skipping log with magic secret api_key.")
                            continue
                        logs_data.append(logs_data_single)
            except json.JSONDecodeError:
                logger.error(f"Invalid log format, not a valid JSON: {log_str}")
                continue
        if not logs_data:
            logger.info("No valid logs to report after processing.")
            return True
        payload = {"items": logs_data}

        try:
            logger.info(f"Reporting {len(logs_data)} logs to {self.report_url}")
            auth_headers = self.generate_auth_headers("post")
            response = await self.client.post(self.report_url, json=payload,headers=auth_headers)
            logger.debug(f"Response: {response.json()}")

            response.raise_for_status()  # 状态码为4xx或5xx时抛出异常
            logger.info(f"Successfully reported {len(logs_data)} logs. Status: {response.status_code}")
            return True
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to report logs, HTTP error: {e.response.status_code}, response: {e.response.text}")
            return False
        except Exception as e:
            logger.error(f"An unexpected error occurred during log reporting: {e}",exc_info=True)
            return False

    async def report_from_main_queue(self):
        """
        从主队列获取并上报日志
        """
        logger.info("report chat log from main_queue")
        logs_str_list_report = await ChatLogRedisManager.get_chat_logs_batch(self.batch_size)
        if not logs_str_list_report:
            logger.info("Main queue is empty.")
            return

        success = await self._send_logs(logs_str_list_report)

        if not success:
            logger.warning(f"Moving {len(logs_str_list_report)} failed logs to dead-letter queue.")
            await ChatLogRedisManager.add_logs_to_dead_letter_queue(logs_str_list_report)

    async def retry_from_dead_letter_queue(self):
        """
        从死信队列获取并尝试重新上报
        """
        logger.info("report chat log from dead_letter_queue")
        # 1. "窥探"日志
        logs_str_list_retry = await ChatLogRedisManager.peek_dead_letter_logs_batch(self.dlq_retry_batch_size)
        if not logs_str_list_retry:
            logger.info("Dead-letter queue is empty.")
            return

        logger.info(f"Attempting to retry {len(logs_str_list_retry)} logs from dead-letter queue.")
        # 2. 尝试发送
        success = await self._send_logs(logs_str_list_retry)

        # 3. 发送成功，才从队列中删除
        if success:
            logger.info(f"Successfully retried and removed {len(logs_str_list_retry)} logs from DLQ.")
            await ChatLogRedisManager.remove_dead_letter_logs_batch(len(logs_str_list_retry))
        else:
            logger.error(f"Retry failed for {len(logs_str_list_retry)} logs. They will remain in DLQ.")


    async def close(self):
        await self.client.aclose()