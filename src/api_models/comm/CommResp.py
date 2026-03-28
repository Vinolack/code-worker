#!/usr/bin/env python3
# -*- coding: UTF-8 -*-
'''
@File    ：CommResp.py
@Author  ：even_lin
@Date    ：2025/5/15 17:11 
@Desc     : {模块描述}
'''

from typing import Generic, TypeVar
from typing import Optional

from pydantic import BaseModel, Field

from src.base.enums.error import BaseErrCodeEnum

T = TypeVar('T')

'''普通返回结果'''
class R(BaseModel,Generic[T]):
    code: str = Field(..., description="响应码")
    message: Optional[str]  = Field(None, description="响应消息")
    data: Optional[T] = Field(None, description="响应数据")

    @staticmethod
    def ok(data: T = None, message: str = None) -> 'R[T]':
        return R(data=data, code=BaseErrCodeEnum.OK.code, message=BaseErrCodeEnum.OK.msg)

    @staticmethod
    def fail(message: str = None, data: T = None, code: str = BaseErrCodeEnum.FAILED.code) -> 'R[T]':
        return R(data=data, code=code, message=message)

    @staticmethod
    def warn(message: str) -> 'R[None]':
        return R(code=BaseErrCodeEnum.WARN.code, message=message,data=None)

    @staticmethod
    def error(message: str = "操作失败") -> 'R[None]':
        return R(code=BaseErrCodeEnum.FAILED.code, message=message,data=None)

    @staticmethod
    def is_success(ret: 'R') -> bool:
        return ret.code == BaseErrCodeEnum.OK.code

    @staticmethod
    def is_error(ret: 'R') -> bool:
        return not R.is_success(ret)
