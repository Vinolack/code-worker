#!/usr/bin/env python3
# -*- coding: UTF-8 -*-
'''
@File    ：ai_model.py
@Author  ：even_lin
@Date    ：2025/6/10 13:55
@Desc     : {模块描述}
'''

import json
from typing import Any, Awaitable, List, cast
from src.base.constants.const import (
    LEGACY_KEY_NAMESPACE_MODULE,
    USER_AI_MODEL_SET_PREFIX,
    build_governed_key,
    build_governed_key_prefix,
)
from src.base.logging import logger
from src.base.utils.uuid_util import UUIDUtil
from src.base.utils.crypto_util import CryptoUtil
from src.config import config
import httpx
from src.dao.redis import RedisManager
from src.services.chat_log import ChatLogReporter

class SyncAiModelJob:
    """
    同步AI模型相关定时任务
    """

    _DEFAULT_CONCURRENCY_FIELDS = (
        "model_default_user_total_concurrency_limit",
        "model_default_user_model_concurrency_limit",
    )

    @staticmethod
    def _legacy_governed_key_prefix(prefix: str) -> str:
        return build_governed_key_prefix(
            prefix=prefix,
            env=config.LEGACY_REDIS_ENV,
            service=config.LEGACY_REDIS_SERVICE,
            module=LEGACY_KEY_NAMESPACE_MODULE,
        )

    @staticmethod
    def _legacy_governed_key(prefix: str, *parts: str) -> str:
        return build_governed_key(
            prefix=prefix,
            env=config.LEGACY_REDIS_ENV,
            service=config.LEGACY_REDIS_SERVICE,
            module=LEGACY_KEY_NAMESPACE_MODULE,
            parts=parts,
        )

    async def sync_ai_model(self):
        """
        同步AI模型
        """
        sync_url = config.SYNC_AI_MODEL_URL
        try:
            logger.info(f"Syncing ai models from {sync_url}")
            auth_headers = ChatLogReporter.generate_auth_headers("get")
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(sync_url, headers=auth_headers)
                response.raise_for_status()  # 状态码为4xx或5xx时抛出异常

            response_json = response.json()
            logger.debug(f"ai_model Response: {response_json}")
            ai_models_data = response_json["data"]
            
            # 处理加密数据的解密
            decrypted_ai_models = self._decrypt_ai_models(ai_models_data)
            
            await self.load_ai_models_to_redis(decrypted_ai_models)
            logger.info(f"Successfully sync ai-models. Status: {response.status_code}")
            return True
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to sync ai-models, HTTP error: {e.response.status_code}, response: {e.response.text}")
            return False
        except Exception as e:
            logger.error(f"An unexpected error occurred during sync ai-models: {e}",exc_info=True)
            return False
        
    async def load_ai_models_to_redis(self, all_ai_models: List[dict]):
        """
        加载所有的AI模型到Redis Hash中。
        """
        logger.info("Starting to load all valid AI models to Redis...")
        try:
            target_key = self._legacy_governed_key_prefix(USER_AI_MODEL_SET_PREFIX)
            if not all_ai_models:
                logger.warning("No valid AI models found in the database. The Redis hash will be cleared.")
                await RedisManager.client.delete(target_key)
                return

            temp_key = self._legacy_governed_key(USER_AI_MODEL_SET_PREFIX, UUIDUtil.generate_random_string(10))

            # 将所有数据写入临时hash key
            hash_data = {}
            for model in all_ai_models:
                ai_model_name = model.get('ai_model')
                if ai_model_name:
                    stored_model = model.copy()
                    if 'upstream_type' in stored_model:
                        upstream_type = stored_model['upstream_type'].split(',')
                    else:
                        upstream_type = ["openai"]  # 默认支持openai类型
                    supported_types = config.SUPPORT_API_TYPES[0] # 第一个软件版本，只支持一个类型
                    if supported_types not in upstream_type:
                        logger.debug(f"AI model '{ai_model_name}' does not support the required API type '{supported_types}'. Add Transform Header.")
                        stored_model['need_transform'] = 1  # 需要做请求转换
                    else:
                        stored_model['need_transform'] = 0  # 不需要转换

                    if "transform_type" not in stored_model:
                        stored_model['transform_type'] = ['openai']  # 默认transform_type为openai

                    self._copy_default_concurrency_fields(model, stored_model)

                    hash_data[ai_model_name] = json.dumps(stored_model, ensure_ascii=False)

                    model_aliases = stored_model.get('model_aliases')
                    if model_aliases and isinstance(model_aliases, str):
                        for alias in model_aliases.split(','):
                            alias_key = alias.strip()
                            if alias_key:
                                alias_model = stored_model.copy()
                                alias_model['is_display_in_worker'] = 0
                                self._copy_default_concurrency_fields(stored_model, alias_model)
                                hash_data[alias_key] = json.dumps(alias_model, ensure_ascii=False)
                                logger.debug(f"Added alias '{alias_key}' for model '{ai_model_name}'")

                    if ai_model_name.lower() != ai_model_name:
                        new_model = stored_model.copy()
                        new_model['is_display_in_worker'] = 0  # 小写的模型不在前端展示
                        self._copy_default_concurrency_fields(stored_model, new_model)
                        hash_data[ai_model_name.lower()] = json.dumps(new_model, ensure_ascii=False)
            if hash_data:
                await cast(Awaitable[Any], RedisManager.client.hset(temp_key, mapping=hash_data))
                await cast(Awaitable[Any], RedisManager.client.rename(temp_key, target_key))

            logger.info(f"Successfully loaded {len(hash_data)} AI models into Redis hash '{target_key}'.")
        except Exception as e:
            logger.error(f"Failed to load AI models to Redis: {e}", exc_info=True)
            raise
    
    @classmethod
    def _copy_default_concurrency_fields(cls, source_model: dict, target_model: dict) -> None:
        for field in cls._DEFAULT_CONCURRENCY_FIELDS:
            if field in source_model:
                target_model[field] = source_model[field]

    def _decrypt_ai_models(self, ai_models_data: List[dict]) -> List[dict]:
        """
        解密AI模型数据中的敏感信息。
        :param ai_models_data: AI模型数据列表
        :return: 解密后的AI模型数据列表
        """
        crypto_util = CryptoUtil(config.WORKER_ENCRYPT_KEY)
        decrypted_models = []
        
        for model in ai_models_data:
            if model["is_display_in_code_plan"] != 1:
                continue  # 只处理在code_plan中展示的模型
            decrypted_model = model.copy()
            if 'code_plan_api_key' in model:
                decrypted_model['api_key'] = crypto_util.decrypt(model['code_plan_api_key'].encode('utf-8'))
            if 'code_plan_base_url' in model:
                decrypted_model['base_url'] = crypto_util.decrypt(model['code_plan_base_url'].encode('utf-8'))
            if 'transform_base_url' in model:
                decrypted_model['transform_base_url'] = crypto_util.decrypt(model['transform_base_url'].encode('utf-8'))
            if 'transform_api_key' in model:
                decrypted_model['transform_api_key'] = crypto_util.decrypt(model['transform_api_key'].encode('utf-8'))
            decrypted_models.append(decrypted_model)
        
        return decrypted_models
