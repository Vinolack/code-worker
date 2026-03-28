#!/usr/bin/python3
# -*- coding: utf-8 -*-
# @Desc: { 模块描述 }
# @Date: 2023/08/30 11:26

from typing import List, Optional, Union

from pydantic import BaseModel, Field

from src.api_models.api_key.perm_msg_code_enum import CodePlanApiKeyPermMsgCodeEnum


class CodePlanApiKeySyncValueResp(BaseModel):
    """code plan api key(v2) 映射值"""
    uid: int = Field(..., description="用户ID")
    level: int = Field(..., description="用户套餐等级: 0试用 1入门 2专业 3旗舰")


class CodePlanApiKeyDigestOnlyResp(BaseModel):
    """API Key摘要校验响应（仅校验digest）"""
    allowed: bool = Field(..., description="是否允许调用")
    cache_seconds: int = Field(default=60, description="缓存时间（秒）")
    perms_group: Optional[List[str]] = Field(default=None, description="权限组编码列表")
    user_id: Optional[Union[str, int]] = Field(default=None, description="用户标识（预留并发限流主体字段）")
    user_total_concurrency_limit: Optional[int] = Field(default=None, description="用户总并发上限（动态配置预留）")
    user_model_concurrency_limit: Optional[int] = Field(default=None, description="用户模型并发上限（动态配置预留）")


class CodePlanApiKeyPermResp(BaseModel):
    """API Key权限响应"""
    type: int = Field(default=1, description="1:仅验证api_key_digest 2:验证api_key_digest、model_name")
    allowed: bool = Field(..., description="是否允许调用")
    cache_seconds: int = Field(default=60, description="缓存时间（秒）")
    plan_remain_quota: Optional[float] = Field(default=0.0, description="codeplan剩余配额")
    sp_remain_quota: Optional[float] = Field(default=0.0, description="加油包剩余配额")
    total_remain_quota: Optional[float] = Field(default=0.0, description="总剩余配额")
    perms_group: Optional[List[str]] = Field(default=None, description="权限组编码列表")
    msg_code: Optional[int] = Field(default=None, description="附加信息代码:200成功，401无效API Key，402无API调用权限（未订阅/无资源包），403无权限调用，404模型不存在，429配额不足，500内部异常")
    user_id: Optional[Union[str, int]] = Field(default=None, description="用户标识")
    user_total_concurrency_limit: Optional[int] = Field(default=None, description="用户总并发上限（动态配置预留）")
    user_model_concurrency_limit: Optional[int] = Field(default=None, description="用户模型并发上限（动态配置预留）")
