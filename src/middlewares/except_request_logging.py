#!/usr/bin/python3
# -*- coding: utf-8 -*-
# @Desc: { 非业务错误响应日志收集中间件 }
# @Date: 2026/01/06

import asyncio
import json
import time
from datetime import datetime
from typing import Optional, Dict, Any

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from src.api_models.except_log.req_model import ExceptRequestLogAddReq
from src.base.logging import logger
from src.base.utils import TraceUtil
from src.config import config
from src.dao.redis.managers.except_log import ExceptLogRedisManager


class ExceptRequestLoggingMiddleware(BaseHTTPMiddleware):
    """记录 HTTP 状态码非 200 且非 401 的请求"""

    MAX_BODY_LENGTH = 1000

    def truncate_string(self, content: str, max_length: int = MAX_BODY_LENGTH) -> str:
        """截断过长的字符串"""
        if len(content) <= max_length:
            return content
        return content[:max_length] + f"... (truncated, total length: {len(content)})"

    def get_client_ip(self, request: Request) -> str:
        """获取客户端真实IP地址
        
        优先级:
        1. X-Real-IP 头
        2. X-Forwarded-For 头的第一个IP
        3. request.client.host
        """
        if request:
            real_ip = request.headers.get("x-real-ip")
            if not real_ip:
                x_forwarded_for = request.headers.get("x-forwarded-for")
                if x_forwarded_for:
                    real_ip = x_forwarded_for.split(',')[0].strip()
            return real_ip or request.client.host
        return "unknown"

    def is_streaming_response(self, response: Response) -> bool:
        """判断是否为流式响应
        
        注意：Starlette 中间件会将所有响应包装成 _StreamingResponse，
        所以不能简单地通过 body_iterator 属性判断。
        需要通过 Content-Type 和 content-length 头来判断。
        """
        # 方法1: 检查 Content-Type 是否为流式类型
        content_type = response.headers.get("content-type", "")
        streaming_types = ["text/event-stream", "application/octet-stream", "multipart/"]
        if any(st in content_type for st in streaming_types):
            return True
        
        # 方法2: 如果没有 content-length 头，通常是真正的流式响应
        # 非流式响应（如 JSONResponse）会有 content-length 头
        if "content-length" not in response.headers:
            return True
        
        return False

    async def parse_request_params(self, request: Request) -> Dict[str, Any]:
        """解析请求参数
        
        支持:
        - Query Parameters
        - JSON Body (application/json)
        """
        params: Dict[str, Any] = {}
        
        # 1. Query Parameters
        if request.query_params:
            params["query_params"] = dict(request.query_params)
        
        # 2. JSON Body
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                json_body = await request.json()
                body_str = str(json_body)
                params["json_body"] = self.truncate_string(body_str)
            except Exception as e:
                logger.debug(f"Failed to parse JSON body: {e}")
                params["json_body"] = "<Failed to parse JSON>"
        
        return params

    def format_request_params(self, params: Dict[str, Any]) -> str:
        """格式化请求参数为日志字符串"""
        if not params:
            return ""
        
        parts = []
        if "query_params" in params:
            parts.append(f"query_params={params['query_params']}")
        if "json_body" in params:
            parts.append(f"json_body={params['json_body']}")
        
        return " | ".join(parts) if parts else ""

    async def parse_response_body(self, response: Response) -> Optional[str]:
        """解析响应体
        在 Starlette 中间件中，响应会被包装成 _StreamingResponse，
        原始的 body 存储在 body_iterator 中。需要消费迭代器来获取内容。
        """
        try:
            # 消费 body_iterator 获取响应内容
            body_chunks = []
            i = 0
            async for chunk in response.body_iterator:
                logger.debug(f"Chunk {i}: {chunk}")
                i += 1
                body_chunks.append(chunk)
            
            # 重新设置 body_iterator
            async def new_iterator():
                for chunk in body_chunks:
                    yield chunk
            response.body_iterator = new_iterator()
            
            # 合并、解码并截断
            body_bytes = b"".join(body_chunks)
            body_str = body_bytes.decode("utf-8")
            return self.truncate_string(body_str)

        except Exception as e:
            logger.exception(f"Failed to parse response body: {e}")
            return f"<Failed to parse: {e}>"

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start_time = time.perf_counter()
        start_datetime = datetime.now()
        call_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        request_id = TraceUtil.get_req_id() or "unknown"
        
        request_params = await self.parse_request_params(request)
        
        response = await call_next(request)
        
        # 流式响应不处理
        if self.is_streaming_response(response):
            return response
        
        http_status = response.status_code
        
        # HTTP 状态码非 200 且非 401 时记录错误日志
        if http_status != 200 and http_status != 401:
            call_time = time.perf_counter() - start_time
            end_datetime = datetime.now()
            response_body_str = await self.parse_response_body(response)
            request_params_str = self.format_request_params(request_params)
            
            # log_message = (
            #     f"[ERROR_RESPONSE] request_id={request_id} | call_at={call_at} | call_time={call_time:.2f}s | "
            #     f"Request: {request.method} {request.url.path}"
            # )
            # if request_params_str:
            #     log_message += f" | {request_params_str}"
            # log_message += f" | Response: http_status={http_status} body={response_body_str}"
            
            # logger.error(log_message)
            
            # 异步写入Redis队列（不阻塞响应返回）
            if config.EXCEPT_LOG_ENABLE:
                asyncio.create_task(self._add_to_queue(
                    request=request,
                    response=response,
                    request_params_str=request_params_str,
                    response_body_str=response_body_str,
                    start_datetime=start_datetime,
                    end_datetime=end_datetime,
                    call_time=call_time
                ))
        
        return response
    
    async def _add_to_queue(
        self,
        request: Request,
        response: Response,
        request_params_str: str,
        response_body_str: str,
        start_datetime: datetime,
        end_datetime: datetime,
        call_time: float
    ):
        """
        异步写入Redis队列的后台任务
        """
        try:
            client_ip = self.get_client_ip(request)
            request_url = str(request.url)
            request_headers = dict(request.headers)
            duration_ms = int(call_time * 1000)

            worker_info = {
                "name": config.SERVER_NAME,
                "port": config.SERVER_PORT
            }

            # 创建日志对象
            log_item = ExceptRequestLogAddReq(
                request_url=request_url,
                method=request.method,
                ip=client_ip,
                status_code=response.status_code,
                request_params=request_params_str if request_params_str else None,
                response=response_body_str,
                worker_info=json.dumps(worker_info, ensure_ascii=False),
                start_time=start_datetime,
                end_time=end_datetime,
                duration=duration_ms
            )
            
            # 写入Redis队列
            await ExceptLogRedisManager.add_except_log(log_item)
            logger.debug(f"Except log added to queue: {request.method} {request.url.path}")
        except Exception as e:
            logger.error(f"Failed to add except log to queue: {e}", exc_info=True)
