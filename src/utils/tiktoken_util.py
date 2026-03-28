#!/usr/bin/env python3
# -*- coding: UTF-8 -*-
'''
@File    ：tiktoken_util.py
@Author  ：even_lin
@Date    ：2025/6/10 17:15 
@Desc     : {模块描述}
'''
from typing import List, Dict

import tiktoken

from src.base.logging import logger


class TiktokenUtil(object):
    """=======================input=============================="""
    @staticmethod
    def count_input_tokens_by_encoding(messages, encoding):
        """
        根据消息列表计算token数量的辅助函数。
        这是基于OpenAI官方cookbook的推荐实现。
        """

        tokens_per_message = 3  # 每个消息都有 <|start|>{role/name}\n{content}<|end|>\n
        tokens_per_name = 1  # 如果有name字段，角色后面会跟着一个name

        num_tokens = 0
        for message in messages:
            num_tokens += tokens_per_message
            for key, value in message.items():
                num_tokens += len(encoding.encode(value))
                if key == "name":
                    num_tokens += tokens_per_name
        num_tokens += 3  # 每个回复都以 <|start|>assistant<|message|> 开始
        return num_tokens

    @staticmethod
    def count_input_tokens_by_model_name(messages,model_name):
        try:
            encoding = tiktoken.encoding_for_model(model_name)
        except KeyError:
            logger.warning("Warning: model not found. Using cl100k_base.")
            encoding = tiktoken.get_encoding("cl100k_base")
        return TiktokenUtil.count_input_tokens_by_encoding(messages, encoding)

    @staticmethod
    def count_input_tokens_by_encoding_name(messages, encoding_name="cl100k_base"):
        try:
            encoding = tiktoken.get_encoding(encoding_name)
        except KeyError:
            logger.warning("Warning: model not found. Using cl100k_base.")
            encoding = tiktoken.get_encoding("cl100k_base")

        return TiktokenUtil.count_input_tokens_by_encoding(messages, encoding)

    """=======================output=============================="""

    @staticmethod
    def count_output_tokens_by_encoding(content:str, encoding):
        return len(encoding.encode(content))

    @staticmethod
    def count_output_tokens_by_model_name(content:str, model_name):
        try:
            encoding = tiktoken.encoding_for_model(model_name)
        except KeyError:
            logger.warning("Warning: model not found. Using cl100k_base.")
            encoding = tiktoken.get_encoding("cl100k_base")
        return TiktokenUtil.count_output_tokens_by_encoding(content, encoding)

    @staticmethod
    def count_output_tokens_by_encoding_name(content:str, encoding_name="cl100k_base"):
        try:
            encoding = tiktoken.get_encoding(encoding_name)
        except KeyError:
            logger.warning("Warning: model not found. Using cl100k_base.")
            encoding = tiktoken.get_encoding("cl100k_base")

        return TiktokenUtil.count_output_tokens_by_encoding(content, encoding)
