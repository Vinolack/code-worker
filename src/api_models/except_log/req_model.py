#!/usr/bin/python3
# -*- coding: utf-8 -*-
# @Desc: { 异常请求日志请求模型 }
# @Date: 2026/01/07

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ExceptRequestLogAddReq(BaseModel):
    """添加异常请求日志请求"""
    request_url: Optional[str] = Field(default=None, description="调用接口URL")
    method: Optional[str] = Field(default=None, description="请求方法 (GET/POST/PUT/DELETE等)")
    ip: Optional[str] = Field(default=None, description="客户端IP")
    status_code: Optional[int] = Field(default=None, description="HTTP响应状态码")
    request_params: Optional[str] = Field(default=None, description="请求参数 (纯文本)")
    response: Optional[str] = Field(default=None, description="响应内容 (纯文本)")
    worker_info: Optional[str] = Field(default=None, description="工作节点信息")
    start_time: Optional[datetime] = Field(default=None, description="调用开始时间")
    end_time: Optional[datetime] = Field(default=None, description="调用结束时间")
    duration: Optional[int] = Field(default=None, description="调用时长 (毫秒)")


class ExceptRequestLogBulkAddReq(BaseModel):
    """批量添加异常请求日志请求"""
    items: list[ExceptRequestLogAddReq] = Field(description="批量添加的异常请求日志记录")
