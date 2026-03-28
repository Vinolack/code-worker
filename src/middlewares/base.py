#!/usr/bin/python3
# -*- coding: utf-8 -*-
# @Desc: { 模块描述 }
# @Date: 2023/07/11 12:17
import time
from http import HTTPStatus

from fastapi import Request
from fastapi.middleware import Middleware
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse
from starlette.datastructures import MutableHeaders
from src.base.enums.error import BizErrCodeEnum
from src.base.exceptions.base import HttpException
from src.base.enums.error import HttpErrCodeEnum
from src.base.logging import logger
from src.base.utils import TraceUtil, context_util, web
from src.config import config
from src.services.api_key import ApiKeyService
from src.middlewares.except_request_logging import ExceptRequestLoggingMiddleware


class LoggingMiddleware(BaseHTTPMiddleware):
    """
    日志中间件
    记录请求参数信息、计算响应时间
    """

    async def set_body(self, request: Request):
        receive_ = await request._receive()

        async def receive():
            return receive_

        request._receive = receive

    async def dispatch(self, request: Request, call_next) -> Response:
        start_time = time.perf_counter()

        # 打印请求信息
        logger.info(f"--> {request.method} {request.url.path} {request.client.host}")
        if request.query_params:
            logger.info(f"--> Query Params: {request.query_params}")

        if "application/json" in request.headers.get("Content-Type", ""):
            await self.set_body(request)
            try:
                # starlette 中间件中不能读取请求数据，否则会进入循环等待 需要特殊处理或者换APIRoute实现
                body = await request.json()
                logger.info(f"--> Body: {body}")
            except Exception as e:
                logger.warning(f"Failed to parse JSON body: {e}")

        # 执行请求获取响应
        response = await call_next(request)

        # 计算响应时间
        process_time = time.perf_counter() - start_time
        response.headers["X-Response-Time"] = f"{process_time:.2f}s"
        logger.info(f"<-- {response.status_code} {request.url.path} (took: {process_time:.2f}s)\n")

        return response


class TraceReqMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # 设置请求id
        request_id = TraceUtil.set_req_id(request.headers.get("X-Request-ID"))
        response = await call_next(request)
        response.headers["X-Request-ID"] = f"{request_id}"  # 记录同一个请求的唯一id
        return response


class GlobalExceptionMiddleware(BaseHTTPMiddleware):
    """全局异常处理中间件"""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        try:
            response = await call_next(request)
            return response
        except Exception as e:
            logger.exception(f"Global unhandled exception: {e}")
            return JSONResponse(
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                content=web.fail_api_resp_with_err_enum(BizErrCodeEnum.SYSTEM_ERR),
            )

class ApiKeyCheckMiddleware(BaseHTTPMiddleware):
    """API KEY 验证中间件"""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path.startswith(config.AUTH_WHITELIST_URLS):
            # 白名单路由，直接放行
            return await call_next(request)

        # 注意：此处token就是api_key的digest
        token = request.headers.get("Authorization") or ""
        token = token.replace("Bearer ", "")
        if token == "":
            # header get x-api-key 兼容旧版本
            token = request.headers.get("x-api-key") or ""
        if not token:
            return JSONResponse(
                status_code=HTTPStatus.UNAUTHORIZED,
                content=web.fail_api_resp_with_code(
                    401, "当前没有填写有效的API Key，请在请求头中添加Authorization字段进行身份验证。"
                ),
            )

        is_digest = True
        if request.url.path.startswith(config.VLM_PROXY_URLS):
            is_digest = False
        else:
            return JSONResponse(
                status_code=HTTPStatus.NOT_FOUND,
                content=web.fail_api_resp_with_code(
                    404, "URL不存在，请检查URL是否正确。"
                ),
            )

        check_result, api_key, auth_info = await ApiKeyService().check_api_key_v3(token, is_digest)
        if not check_result:
            return JSONResponse(
                status_code=HTTPStatus.UNAUTHORIZED,
                content=web.fail_api_resp_with_code(
                    401,
                    "无效的API Key ，请检查：\n1. API Key是否正确\n2. 是否已被禁用或删除\n3. 是否过期"
                ),
            )

        # 将api_key重新写入请求头（确认都是digest）
        new_headers = MutableHeaders(request.headers)
        new_headers["Authorization"] = api_key
        request.scope['headers'] = new_headers.raw
        
        user_info = {}
        if auth_info:
            user_info["api_key_digest"] = api_key
            user_info["perms_group"] = auth_info.perms_group
        context_util.USER_CTX.set(user_info)
        response = await call_next(request)
        return response

def register_middlewares():
    """注册中间件（逆序执行）"""
    return [
        # Middleware(LoggingMiddleware),
        Middleware(
            CORSMiddleware,
            allow_origins=config.ALLOW_ORIGINS,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        ),
        Middleware(TraceReqMiddleware),
        Middleware(ExceptRequestLoggingMiddleware),  # 异常响应日志收集
        Middleware(ApiKeyCheckMiddleware),
        #Middleware(AuthMiddleware),
        Middleware(GlobalExceptionMiddleware),
    ]
