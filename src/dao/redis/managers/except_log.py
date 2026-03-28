#!/usr/bin/env python3
# -*- coding: UTF-8 -*-
'''
@File    ：except_log.py
@Author  ：even_lin
@Date    ：2026/01/07
@Desc     : 异常日志Redis队列管理
'''

from src.api_models.except_log.req_model import ExceptRequestLogAddReq
from src.dao.redis import RedisManager

# 异常日志队列
EXCEPT_LOG_QUEUE_KEY = "except_log_queue"


class ExceptLogRedisManager(RedisManager):
    """
    异常日志数据访问对象
    """

    @classmethod
    async def add_except_log(cls, log_item: ExceptRequestLogAddReq):
        """
        将单条异常日志推入Redis队列（List的右侧）
        """
        log_str = log_item.model_dump_json()
        await cls.client.rpush(EXCEPT_LOG_QUEUE_KEY, log_str)

    @classmethod
    async def get_except_logs_batch(cls, batch_size: int = 500) -> list[str]:
        """
        从队列左侧批量获取日志，并从队列中移除
        原子操作
        """
        # pipeline保证原子性
        pipe = cls.client.pipeline()
        # 从列表左边获取 batch_size 个元素
        pipe.lrange(EXCEPT_LOG_QUEUE_KEY, 0, batch_size - 1)
        # 裁剪列表，保留 batch_size 之后的所有元素
        pipe.ltrim(EXCEPT_LOG_QUEUE_KEY, batch_size, -1)

        logs_str, _ = await pipe.execute()

        if not logs_str:
            return []

        return logs_str
