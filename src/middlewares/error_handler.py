#!/usr/bin/python3
# -*- coding: utf-8 -*-
# @Desc: { 项目统一错误处理模块 }
# @Date: 2023/07/10 16:41
from http import HTTPStatus
import json

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from src.base.exceptions import BizException
from src.base.exceptions.base import HttpException
from src.base.logging import logger

from src.base.enums.error import BizErrCodeEnum
from src.base.enums.error import HttpErrCodeEnum
from src.base.utils import web

from src.utils.http_client import HttpErrorWithContent



async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """全局捕捉参数验证异常"""

    # 格式化参数校验错误信息
    errors = exc.errors()
    formatted_errors = [
        {"field": ".".join(str(loc) for loc in error["loc"]), "message": error["msg"]} for error in errors
    ]
    error_tip = f"参数校验错误 {formatted_errors}"
    logger.error(error_tip)

    error_detail = {"error_detail": formatted_errors}

    return JSONResponse(
        status_code=HTTPStatus.OK,  # 200
        content=web.fail_api_resp_with_err_enum(BizErrCodeEnum.PARAM_ERR, "Validation error", error_detail),
    )


async def global_exception_handler(request: Request, exc: Exception):
    """全局系统异常处理器"""

    if isinstance(exc, ConnectionError):
        message = f"网络异常, {exc}"
        err_enum = BizErrCodeEnum.SOCKET_ERR
    else:
        message = f"系统异常, {exc}"
        err_enum = BizErrCodeEnum.SYSTEM_ERR

    logger.exception(f"global_exception_handler {message}")
    return JSONResponse(status_code=HTTPStatus.INTERNAL_SERVER_ERROR, content=web.fail_api_resp_with_err_enum(err_enum))


async def biz_error_handler(request: Request, exc: BizException):
    """业务错误处理，返回200状态码"""
    logger.error(f"biz_error_handler {exc} {exc.msg}")
    return JSONResponse(status_code=HTTPStatus.OK, content=web.fail_api_resp(exc.msg))

async def http_error_handler(request: Request, exc: HttpException):
    """HTTP错误处理，返回对应状态码"""
    logger.error(f"http_error_handler {exc} {exc.msg}")
    http_code = int(exc.code) if exc.code.isdigit() else HTTPStatus.INTERNAL_SERVER_ERROR
    return JSONResponse(status_code=http_code, content=web.fail_api_resp_with_code(int(exc.code), exc.msg))

async def http_upstream_error_handler(request: Request, exc: HttpErrorWithContent):
    """上游HTTP错误处理，返回对应状态码和内容"""
    logger.error(f"http_upstream_error_handler status={exc.status_code} content={exc.content}")

    # 特殊处理：401、402、403错误码转换为500并使用静态错误信息
    if exc.status_code in {401, 402, 403}:
        logger.warning(f"Converting upstream {exc.status_code} to 500 for client (hiding upstream details)")
        return JSONResponse(
            status_code=500,
            content=web.fail_api_resp_with_code(500, "系统异常，请联系客服获取支持。")
        )

    # 特殊处理：47x错误码（内部专有状态）转换为503
    if exc.status_code and 470 <= exc.status_code <= 479:
        logger.warning(f"Converting upstream {exc.status_code} to 503 for client (server overloaded)")
        return JSONResponse(
            status_code=503,
            content=web.fail_api_resp_with_code(503, "当前服务器负载过大，请稍后再试")
        )
    
    # 处理400状态码的特殊情况（不恰当内容）
    if exc.status_code == 400 and exc.content:
        try:
            # 尝试解析错误内容
            content_str = exc.content.decode('utf-8') if isinstance(exc.content, bytes) else str(exc.content)
            content_lower = content_str.lower()
            
            # 检查是否包含aliyun
            should_rewrite = 'aliyun' in content_lower
            
            # 如果不包含aliyun，尝试解析JSON检查智谱AI的错误码
            if not should_rewrite:
                try:
                    error_json = json.loads(content_str)
                    # 检查智谱AI的错误码1300和1301
                    if 'error' in error_json and isinstance(error_json['error'], dict):
                        error_code = error_json['error'].get('code')
                        if error_code in ['1300', '1301', 1300, 1301]:
                            should_rewrite = True
                    elif 'code' in error_json:
                        error_code = error_json.get('code')
                        if error_code in ['1300', '1301', 1300, 1301]:
                            should_rewrite = True
                except (json.JSONDecodeError, ValueError):
                    pass
            
            # 重写返回内容
            if should_rewrite:
                logger.info(f"Rewriting 400 error response for inappropriate content")
                rewritten_response = {
                    "code": 400,
                    "message": "您的输入包含不恰当的内容，请修改后再试",
                    "data": {}
                }
                return JSONResponse(
                    status_code=400,
                    content=rewritten_response
                )
        except Exception as e:
            logger.warning(f"Failed to parse or rewrite 400 error content: {e}")
    
    # 其他错误码保持原样返回
    if exc.content is None:
        exc.content = "Upstream HTTP error with no content"
    http_code = exc.status_code if exc.status_code else HTTPStatus.INTERNAL_SERVER_ERROR
    return Response(status_code=http_code, content=exc.content)


def register_exception_handler():
    """注册异常处理器"""
    return {
        RequestValidationError: validation_exception_handler,  # 请求参数校验错误处理
        HttpException: http_error_handler,  # HTTP错误处理
        HttpErrorWithContent: http_upstream_error_handler,  # 上游HTTP错误处理
        BizException: biz_error_handler,  # 业务错误处理
        Exception: global_exception_handler,  # 全局未捕获的异常处理(默认走中间件处理)
    }
