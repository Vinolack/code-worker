#!/usr/bin/python3
# -*- coding: utf-8 -*-
# @Desc: { 心跳与健康检查模块 }
# @Date: 2026/01/12

from fastapi import status
from src.routers.base import BaseAPIRouter
from fastapi.responses import PlainTextResponse
from src.dao.redis.client import RedisManager
from src.base.logging import logger

router = BaseAPIRouter(prefix="/status", api_log=False)


@router.get("/ping", summary="心跳检测", include_in_schema=True)
async def ping():
    """
    心跳检测 - 检查服务及 Redis 连接状态
    
    返回:
        - 200: 服务正常，Redis 连接正常
        - 500: Redis 连接失败
    """
    try:
        # 检查 Redis 连接
        await RedisManager.client.ping()
        
        return PlainTextResponse(
            content="pong", 
            status_code=status.HTTP_200_OK
        )
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        return PlainTextResponse(
            content="Service Unavailable",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
