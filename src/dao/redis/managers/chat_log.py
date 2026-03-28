#!/usr/bin/env python3
# -*- coding: UTF-8 -*-
'''
@File    ：chat_log.py
@Author  ：even_lin
@Date    ：2025/6/18 15:06 
@Desc     : {模块描述}
'''

from typing import List

from src.api_models.chat_app.req_model import ChatLogAddReq
from src.dao.redis import RedisManager

#chatlog队列
CHAT_LOG_QUEUE_KEY = "chat_log_queue"
#死信队列
CHAT_LOG_DEAD_LETTER_QUEUE_KEY = "chat_log_dead_letter_queue"


class ChatLogRedisManager(RedisManager):
    """
    聊天日志数据访问对象
    """

    @classmethod
    async def add_chat_log(cls, log_item: ChatLogAddReq):
        """
        将单条日志推入Redis队列（List的右侧）
        """
        log_str = log_item.model_dump_json()
        await cls.client.rpush(CHAT_LOG_QUEUE_KEY, log_str)

    @classmethod
    async def get_chat_logs_batch(cls, batch_size: int = 500) -> list[str]:
        """
        从队列左侧批量获取日志，并从队列中移除
        原子操作
        """
        # pipeline保证原子性
        pipe = cls.client.pipeline()
        # 从列表左边获取 batch_size 个元素
        pipe.lrange(CHAT_LOG_QUEUE_KEY, 0, batch_size - 1)
        # 裁剪列表，保留 batch_size 之后的所有元素
        pipe.ltrim(CHAT_LOG_QUEUE_KEY, batch_size, -1)

        logs_str, _ = await pipe.execute()

        if not logs_str:
            return []

        return logs_str

    @classmethod
    async def add_logs_to_dead_letter_queue(cls, logs_str_list: list[str]):
        """
        将一批日志推入死信队列
        """
        if not logs_str_list:
            return

        await cls.client.rpush(CHAT_LOG_DEAD_LETTER_QUEUE_KEY, *logs_str_list)

    @classmethod
    async def peek_dead_letter_logs_batch(cls, batch_size: int) -> List[str]:
        """
        "窥探"死信队列头部的日志，但不移除它们
        """
        return await cls.client.lrange(CHAT_LOG_DEAD_LETTER_QUEUE_KEY, 0, batch_size - 1)

    @classmethod
    async def remove_dead_letter_logs_batch(cls, count: int):
        """
        从死信队列的头部移除指定数量的日志
        """
        if count > 0:
            await cls.client.ltrim(CHAT_LOG_DEAD_LETTER_QUEUE_KEY, count, -1)
