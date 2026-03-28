#!/usr/bin/python3
# -*- coding: utf-8 -*-
# @Desc: { 模块描述 }
# @Date: 2023/08/30 11:26
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field
from datetime import datetime


class Message(BaseModel):
    role: str = Field(..., description="角色")
    content: str = Field(..., description="内容")

class ChatReq(BaseModel):
    """chat入参"""
    messages: list[Message] = Field(..., description="消息列表")
    stream: bool  = Field(True, description="是否流式返回")
    model: str = Field(description="模型")
    temperature: Optional[float] = Field(default=None,ge=0, le=2,description="温度")
    max_tokens: Optional[int] = Field(default=None,ge=1,description="最大token数量")
    frequency_penalty: Optional[float] = Field(default=None,description="频率惩罚")
    tools: Optional[list[dict]] = Field(default=None, description="可用的工具列表")


class ChatLogAddReq(BaseModel):
    order_no: str = Field(description="订单流水号")
    call_time: datetime = Field(description="调用时间")
    api_key: str = Field(description="调用的api key(此处是摘要)")
    ip_address: str = Field(description="访问 ip")
    model_name:str = Field(description="模型名称")
    real_model_name:str = Field(description="真实模型名称")
    use_time: int = Field(description="调用耗时")
    input_tokens: int = Field(description="输入的token数量")
    output_tokens: int = Field(description="输出的token数量")
    status: int = Field(description="调用状态:0:失败 1:成功")
    tools_count: int = Field(default=0, description="请求中可用的tools数量")

class ChatLogBulkAddReq(BaseModel):
    items: list[ChatLogAddReq] = Field(description="批量添加的chat调用记录")











