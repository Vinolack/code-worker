#!/usr/bin/env python3
# -*- coding: UTF-8 -*-
import hashlib
import base64

class ApiKeyDigestUtil(object):
    @staticmethod
    def create_short_key_digest(api_key: str) -> str:
        """
        为给定的API Key生成一个简短且安全的SHA-256摘要。
        """
        if not isinstance(api_key, str):
            raise TypeError("API Key 必须是字符串。")

        digest_bytes = hashlib.sha256(api_key.encode('utf-8')).digest()
        short_digest = base64.b64encode(digest_bytes).decode('utf-8').rstrip('=')

        return short_digest