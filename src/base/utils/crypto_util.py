#!/usr/bin/env python3
# -*- coding: UTF-8 -*-
'''
@File    ：crypto_util.py
@Author  ：even_lin
@Date    ：2025/6/27 15:13 
@Desc     : {模块描述}
'''
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken


class CryptoUtil:
    """
    对称加解密工具类。
    """

    def __init__(self, key: str):
        """
        使用指定的密钥初始化加密器。
        :param key: 用于加解密的密钥字符串。必须是32字节、URL安全的base64编码字符串。
        """
        if not isinstance(key, str) or not key:
            raise ValueError("密钥必须是一个非空的字符串。")
        try:
            self.fernet_instance = Fernet(key.encode('utf-8'))
        except (ValueError, TypeError) as e:
            raise ValueError(f"提供的密钥无效或格式不正确: {e}")

    @staticmethod
    def generate_key() -> str:
        """
        生成一个符合 Fernet 规范的新密钥。
        :return: 一个 URL 安全的 base64 编码的32字节密钥字符串。
        """
        return Fernet.generate_key().decode('utf-8')

    def encrypt(self, plaintext: str) -> bytes:
        """
        使用实例中保存的密钥来加密字符串。

        :param plaintext: 需要加密的原始字符串。
        :return: 加密后的二进制数据 (bytes)。
        """
        if not isinstance(plaintext, str):
            raise TypeError("加密的输入必须是字符串。")

        return self.fernet_instance.encrypt(plaintext.encode('utf-8'))

    def decrypt(self, encrypted_data: bytes) -> Optional[str]:
        """
        解密
        :param encrypted_data: 需要解密的二进制数据。
        :return: 解密后的原始字符串，如果解密失败则返回 None。
        """
        if not isinstance(encrypted_data, bytes):
            raise TypeError("解密的输入必须是二进制(bytes)。")

        try:
            decrypted_bytes = self.fernet_instance.decrypt(encrypted_data)
            return decrypted_bytes.decode('utf-8')
        except InvalidToken:
            return None  # 密钥不匹配或令牌无效，静默失败并返回None

if __name__ == "__main__":
    # 示例用法
    key = CryptoUtil.generate_key()
    print(f"Generated Key: {key}")
    crypto_util = CryptoUtil(key)

    original_text = "Hello, World!"
    encrypted_text = crypto_util.encrypt(original_text)
    print(f"Encrypted: {encrypted_text}")

    decrypted_text = crypto_util.decrypt(encrypted_text)
    print(f"Decrypted: {decrypted_text}")
    
    # 测试解密失败
    invalid_decrypt = crypto_util.decrypt(b"invalid data")
    print(f"Invalid Decrypt: {invalid_decrypt}")  # 应该返回 None