#!/usr/bin/python3
# -*- coding: utf-8 -*-
# @Desc: { 上下文模块描述 }
# @Date: 2023/10/30 15:11
import contextvars
from typing import Union

from fastapi import Request

from src.base.exceptions.base import BizException



# 请求对象上下文
REQUEST_CTX: contextvars.ContextVar[Union[Request, None]] = contextvars.ContextVar("request", default=None)


# 请求唯一id
REQUEST_ID: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")

# 任务追踪唯一id
TRACE_ID: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")

# 用户信息
USER_CTX: contextvars.ContextVar[dict] = contextvars.ContextVar("user_info", default={})

def current_user():
    if not USER_CTX.get():
        raise BizException("用户状态异常，请联系客服处理")
    return USER_CTX.get()


def current_user_api_key_digest():
    if not USER_CTX.get():
        raise BizException("用户状态异常，请联系客服处理")
    return USER_CTX.get().get("api_key_digest", None)

def current_user_perms_group():
    user_info = USER_CTX.get()
    if not user_info:
        raise BizException("用户状态异常，请联系客服处理")
    perms = user_info.get("perms_group",[])
    if perms is None:
        return []
    return perms