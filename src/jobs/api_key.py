#!/usr/bin/env python3
# -*- coding: UTF-8 -*-
'''
@File    ：chat_log.py
@Author  ：even_lin
@Date    ：2025/6/10 13:55
@Desc     : {模块描述}
'''

from src.base.logging import logger
from src.services.api_key import ApiKeyService
from src.services.chat_log import ChatLogReporter

class SyncApiKeysJob:
    """
    同步ApiKey定时任务
    """
    def __init__(self):
        self.service = ApiKeyService()
        logger.info("ApiKeyService instance created for SyncChatLogJob.")

    async def sync_api_keys(self):
        """
        定时从api-serve同步api-keys
        """
        await self.service.sync_api_keys()

