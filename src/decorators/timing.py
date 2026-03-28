#!/usr/bin/env python3
# -*- coding: UTF-8 -*-
'''
@File    ：timing.py
@Author  ：even_lin
@Date    ：2026/2/4
@Desc     : 计时装饰器
'''
import time
import functools
import inspect
from typing import Callable, Optional, Literal
from src.base.logging import logger


TimeUnit = Literal['s', 'ms', 'us']


def timing_logger(
    prefix: Optional[str] = None,
    log_level: Literal['debug', 'info', 'warning', 'error'] = 'info',
    time_unit: TimeUnit = 'ms'
):
    """
    计时装饰器，用于记录函数执行时间
    
    :param prefix: 日志前缀，默认为函数名
    :param log_level: 日志级别，可选 'debug', 'info', 'warning', 'error'，默认 'info'
    :param time_unit: 时间单位，可选 's'(秒), 'ms'(毫秒), 'us'(微秒)，默认 'ms'
    
    使用示例:
        @timing_logger(prefix="API调用", log_level="debug", time_unit="ms")
        async def fetch_data():
            pass
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            func_name = prefix or func.__name__
            start_time = time.perf_counter()
            
            try:
                result = await func(*args, **kwargs)
                elapsed = time.perf_counter() - start_time
                _log_timing(func_name, elapsed, log_level, time_unit)
                return result
            except Exception as e:
                elapsed = time.perf_counter() - start_time
                _log_timing(func_name, elapsed, log_level, time_unit, error=str(e))
                raise
        
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            func_name = prefix or func.__name__
            start_time = time.perf_counter()
            
            try:
                result = func(*args, **kwargs)
                elapsed = time.perf_counter() - start_time
                _log_timing(func_name, elapsed, log_level, time_unit)
                return result
            except Exception as e:
                elapsed = time.perf_counter() - start_time
                _log_timing(func_name, elapsed, log_level, time_unit, error=str(e))
                raise
        
        # 判断是否是协程函数
        if inspect.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator


def _log_timing(
    func_name: str,
    elapsed: float,
    log_level: str,
    time_unit: TimeUnit,
    error: Optional[str] = None
):
    """
    记录时间日志
    
    :param func_name: 函数名称
    :param elapsed: 耗时（秒）
    :param log_level: 日志级别
    :param time_unit: 时间单位
    :param error: 错误信息（可选）
    """
    # 转换时间单位
    if time_unit == 's':
        elapsed_formatted = f"{elapsed:.3f}s"
    elif time_unit == 'ms':
        elapsed_formatted = f"{elapsed * 1000:.3f}ms"
    elif time_unit == 'us':
        elapsed_formatted = f"{elapsed * 1000000:.3f}us"
    else:
        elapsed_formatted = f"{elapsed:.3f}s"
    
    # 构造日志消息
    if error:
        msg = f"{func_name} failed, elapsed: {elapsed_formatted}, error: {error}"
    else:
        msg = f"{func_name} completed, elapsed: {elapsed_formatted}"
    
    # 根据日志级别输出
    log_func = getattr(logger, log_level.lower(), logger.info)
    log_func(msg)
