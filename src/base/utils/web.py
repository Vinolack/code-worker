#!/usr/bin/python3
# -*- coding: utf-8 -*-
# @Desc: { 模块描述 }
# @Date: 2023/09/06 16:49
from src.api_models.comm.CommResp import R
from src.base.enums.error import BaseErrCode
from src.base.enums.error import BizErrCodeEnum
from src.base.enums.error import HttpErrCodeEnum

from typing import Any, Union, Dict


def fail_api_resp_with_err_enum(err_enum: BaseErrCode, err_msg: Union[str,None] = None, data: Union[Dict[str,Any],None]=None):
    """失败的响应携带错误码"""
    # return R.fail(code=err_enum.code,data=data,message=err_msg or err_enum.msg).model_dump()

    resp_content = {
        "code": err_enum.code,
        "message": err_msg or err_enum.msg,
        "data": data or {},
    }
    return resp_content

def fail_api_resp(err_msg: Union[str,None] = None, data: Union[Dict[str,Any],None]=None):
    """失败的响应 默认Failed错误码"""
    # return R.fail(code=BizErrCodeEnum.FAILED.code, data=data, message=err_msg or BizErrCodeEnum.FAILED.msg).model_dump()
    resp_content = {
        "code": BizErrCodeEnum.FAILED.code,
        "message": err_msg or BizErrCodeEnum.FAILED.msg,
        "data": data or {},
    }
    return resp_content

def fail_api_resp_with_code(err_code: int, err_msg: Union[str,None] = None, data: Union[Dict[str,Any],None]=None):
    """失败的响应携带特定错误码"""
    # return R.fail(code=err_code.code,data=data,message=err_msg or err_code.msg).model_dump()
    resp_content = {
        "code": err_code,
        "message": err_msg or "Failed",
        "data": data or {},
    }
    return resp_content

