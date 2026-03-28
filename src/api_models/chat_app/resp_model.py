#!/usr/bin/python3
# -*- coding: utf-8 -*-
# @Desc: { 模块描述 }
# @Date: 2023/08/30 11:26
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field


class AllAiModelListResp(BaseModel):
    """获取所有AI模型响应模型"""
    ai_model: str = Field(description="模型名称")
    base_url: Optional[str] = Field(description="基础URL")
    api_key: Optional[str] = Field(description="API密钥")
