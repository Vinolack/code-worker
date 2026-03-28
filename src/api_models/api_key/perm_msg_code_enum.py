#!/usr/bin/python3
# -*- coding: utf-8 -*-
# @Desc: { 模块描述 }
# @Date: 2023/08/30 11:26

from enum import Enum


class CodePlanApiKeyPermMsgCodeEnum(Enum):
    """API Key权限验证消息码枚举"""
    SUCCESS = (200, "成功")
    INVALID_API_KEY = (401, "无效的API Key ，请检查：\n1. API Key是否正确\n2. 是否已被禁用或删除\n3. 是否过期")
    MODEL_NOT_OPEN = (403, "该模型暂未开放调用，请联系客服处理～")
    NO_API_PERMISSION = (402, "无API调用权限（未订阅相关codeplan或资源包）～")
    NO_MODEL_PERMISSION = (403, "当前codeplan或资源包订阅无该模型调用权限～")
    QUOTA_INSUFFICIENT = (429, "当前codeplan或资源包订阅，所剩配额不足～")
    MODEL_NOT_EXIST = (404, "模型不存在，请检查模型名称参数～")
    INTERNAL_ERROR = (500, "系统内部异常～")

    def __init__(self, code: int, desc: str):
        self.code = code
        self.desc = desc

    @classmethod
    def get_by_code(cls, code: int):
        for item in cls:
            if item.code == code:
                return item
        return None
