#!/usr/bin/python3
# -*- coding: utf-8 -*-
# @Desc: { redis 客户端模块 }
# @Date: 2023/07/10 21:23
from src.base.redis import BaseRedisManager


class RedisManager(BaseRedisManager):
    @classmethod
    async def set_ex_with_px(cls, key, value, px):
        """
        根据 设置 Redis 缓存
        """
        await cls.client.setex(key, value, px)

    @classmethod
    async def set_nx_with_px(cls, key, value, px) -> bool:
        """
        使用 NX 参数设置 Redis 缓存，并设置过期时间
        :param key: 缓存的键
        :param value: 缓存的值
        :param px: 过期时间（毫秒）
        :return:
        """
        return await cls.client.set(key, value, nx=True, px=px)

    @classmethod
    async def get(cls, key):
        cache_info = await cls.client.get(key)
        return cache_info


    @classmethod
    async def delete(cls,key):
        await cls.client.delete(key)

