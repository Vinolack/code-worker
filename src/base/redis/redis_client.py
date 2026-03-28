#!/usr/bin/python3
# -*- coding: utf-8 -*-
# @Desc: { redis连接处理模块 }
# @Date: 2023/05/03 21:13
from typing import Optional, Union

from redis import Redis
from redis import asyncio as aioredis

from src.base import constants


class BaseRedisManager:
    """Redis客户端管理器"""

    client: Union[Redis, aioredis.Redis] = None
    cache_key_prefix = constants.CACHE_KEY_PREFIX

    @classmethod
    def init_redis_client(
        cls,
        async_client: bool = False,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        password: Optional[str] = None,
        max_connections: Optional[int] = 100,
        **kwargs
    ):
        """
        初始化 Redis 客户端。

        Args:
            async_client (bool): 是否使用异步客户端，默认为 False（同步客户端）
            host (str): Redis 服务器的主机名，默认为 'localhost'
            port (int): Redis 服务器的端口，默认为 6379
            db (int): 要连接的数据库编号，默认为 0
            password (Optional[str]): 密码可选
            max_connections (Optional[int]): 最大连接数。默认为 None（不限制连接数）
            **kwargs: 传递给 Redis 客户端的其他参数

        Returns:
            None
        """
        if cls.client is None:
            redis_client_cls = Redis
            if async_client:
                redis_client_cls = aioredis.Redis

            cls.client = redis_client_cls(
                host=host, port=port, db=db, password=password, max_connections=max_connections,decode_responses=True, **kwargs
            )

        return cls.client


