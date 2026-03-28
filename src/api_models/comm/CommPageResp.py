#!/usr/bin/env python3
# -*- coding: UTF-8 -*-
'''
@File    ：CommPageResp.py
@Author  ：even_lin
@Date    ：2025/5/15 19:39 
@Desc     : {通用分页响应}
'''
from typing import Generic, TypeVar, List

from pydantic import Field

from src.api_models.comm.CommResp import R
from src.base.enums.error import BaseErrCodeEnum

T = TypeVar('T')

class P(R[List[T]], Generic[T]):
    total: int = Field(None, description="总条数")

    @staticmethod
    def ok(total: int, data: List[T]) -> 'P[T]':
        """创建成功的分页响应"""
        return P(
            total=total,
            data=data,
            code=BaseErrCodeEnum.OK.code,
            message="查询成功"
        )


