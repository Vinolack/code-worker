#!/usr/bin/env python3
# -*- coding: UTF-8 -*-
'''
@File    ：chat_app.py
@Author  ：even_lin
@Date    ：2025/6/10 13:55
@Desc     : {模块描述}
'''

import json
from collections.abc import AsyncGenerator
from typing import Any, Union

from fastapi import Request

from src.utils.http_client import HttpErrorWithContent
from src.base.exceptions import HttpException

from src.api_models.chat_app.req_model import ChatReq
from src.base.logging import logger
from src.services.vlm import VlmService

OPENAI_API_PARAMS = {
    "model",
    "messages",
    "stream",
    "temperature",
    "max_tokens",
    "frequency_penalty",
}


class ChatAppService:
    pass
    # 这玩意被弃用了，直接调用VlmService了