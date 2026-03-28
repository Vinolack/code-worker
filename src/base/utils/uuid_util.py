#!/usr/bin/env python3
# -*- coding: UTF-8 -*-
'''
@File    ：uuid_util.py
@Author  ：even_lin
@Date    ：2025/5/24 14:55 
@Desc     : {模块描述}
'''
import uuid
import hashlib


class UUIDUtil:
    @staticmethod
    def generate_uuid_v4():
        """
        生成一个 UUIDv4 字符串（不含连字符）
        """
        return str(uuid.uuid4()).replace('-', '')

    @staticmethod
    def generate_random_string(length=32):
        """
        生成指定长度的随机字符串（基于 UUIDv4）

        :param length: 需要生成的随机字符串长度，默认为 32
        :return: 指定长度的随机字符串
        """
        if not isinstance(length, int) or length <= 0:
            raise ValueError("Length must be a positive integer")

        # 使用 UUIDv4 作为随机种子生成更安全的随机字符串
        raw_uuid = uuid.uuid4().hex  # 获取 UUIDv4 的 hex 形式（32位无符号字符串）

        # 如果长度超过 32，使用哈希扩展
        if length > 32:
            hash_obj = hashlib.sha256(raw_uuid.encode())
            extended = hash_obj.hexdigest()  # 64位十六进制字符串
            return extended[:length]

        # 若长度小于等于 32，直接截取并返回
        return raw_uuid[:length]
