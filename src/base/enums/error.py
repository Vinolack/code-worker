#!/usr/bin/python3
# -*- coding: utf-8 -*-
# @Desc: { 错误码枚举 }
# @Date: 2023/09/09 14:45


class BaseErrCode:
    def __init__(self, code, msg):
        self.code = code
        self.msg = msg


class BaseErrCodeEnum:
    """
    Notes：由于枚举不能继承成员故改成普通类方式
    错误码前缀
     - 000-通用基础错误码前缀
     - 100-待定
     - 200-通用业务错误码前缀
        eg:
        - 201-用户模块
        - 202-订单模块
     - 300-待定
     - 400-通用请求错误
     - 500-通用系统错误码前缀
    """

    OK = BaseErrCode("200", "SUCCESS")
    FAILED = BaseErrCode("500", "FAILED")
    WARN = BaseErrCode("600", "WARN")
    FUNC_TIMEOUT_ERR = BaseErrCode("000-0002", "最大超时错误")
    FUNC_RETRY_ERR = BaseErrCode("000-0003", "最大重试错误")
    SEND_SMS_ERR = BaseErrCode("000-0004", "发送短信错误")
    SEND_EMAIL_ERR = BaseErrCode("000-0005", "发送邮件错误")

    BAD_REQUEST_ERR = BaseErrCode("400", "错误请求")
    AUTH_ERR = BaseErrCode("401", "权限认证错误")
    PAYMENT_REQUIRED_ERR = BaseErrCode("402", "需要支付")
    FORBIDDEN_ERR = BaseErrCode("403", "无权限访问")
    NOT_FOUND_ERR = BaseErrCode("404", "未找到资源错误")
    REQUEST_TIMEOUT = BaseErrCode("408", "请求超时错误")
    PARAM_ERR = BaseErrCode("422", "参数错误")

    SYSTEM_ERR = BaseErrCode("500", "系统异常")
    SOCKET_ERR = BaseErrCode("501", "网络异常")
    GATEWAY_ERR = BaseErrCode("502", "网关异常")
    SERVICE_UNAVAILABLE_ERR = BaseErrCode("503", "服务不可用异常")
    GATEWAY_TIMEOUT_ERR = BaseErrCode("504", "网关超时异常")
    WEB_SERVER_DOWN_ERR = BaseErrCode("521", "Web服务器异常")
    CONNECT_TIMEOUT_ERR = BaseErrCode("522", "连接超时异常")

class BizErrCodeEnum(BaseErrCodeEnum):
    """
    错误码前缀
     - 000-通用基础错误码前缀
     - 100-待定
     - 200-通用业务错误码前缀
        eg:
        - 201-用户模块
        - 202-订单模块
     - 300-待定
     - 400-通用请求错误
     - 500-通用系统错误码前缀
    """


class HttpErrCodeEnum(BaseErrCodeEnum):
    """
    HTTP相关错误码前缀
    """
