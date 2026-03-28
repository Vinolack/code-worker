#!/usr/bin/python3
# -*- coding: utf-8 -*-
# @File: const.py
# @Desc: { 常量模块 }
# @Date: 2024/07/23 13:49
from pathlib import Path
from typing import Iterable

# 编码格式
UTF8 = "UTF-8"
GBK = "GBK"

WWW = "WWW"

# 默认的缓存key前缀
CACHE_KEY_PREFIX = "ys-tools"
USER_API_KEY_SET_PREFIX = "user-api-key"
USER_AI_MODEL_SET_PREFIX = "user-ai-model"

# code-plan user api key auth cache (v2 api-key check)
CODE_PLAN_USER_API_KEY_AUTH_CACHE_PREFIX = "code-plan-user-api-key-auth"

# code-plan api key digest only cache (v3 api-key check)
CODE_PLAN_API_KEY_DIGEST_ONLY_CACHE_PREFIX = "code-plan-api-key-digest-only"

# code-plan api key perm cache (perm api check)
CODE_PLAN_API_KEY_PERM_CACHE_PREFIX = "code-plan-api-key-perm"

KEY_NAMESPACE_SEPARATOR = ":"
KEY_NAMESPACE_TEMPLATE = "{env}:{service}:{module}"

LIMITER_KEY_NAMESPACE_MODULE = "limiter"
LIMITER_KEY_PREFIX = "limiter"
LIMITER_USER_TOTAL_KEY_SCOPE = "user-total"
LIMITER_USER_MODEL_KEY_SCOPE = "user-model"

LEGACY_KEY_NAMESPACE_MODULE = "legacy"
LEGACY_GOVERNED_KEY_PREFIXES = (
    USER_API_KEY_SET_PREFIX,
    USER_AI_MODEL_SET_PREFIX,
    CODE_PLAN_USER_API_KEY_AUTH_CACHE_PREFIX,
    CODE_PLAN_API_KEY_DIGEST_ONLY_CACHE_PREFIX,
    CODE_PLAN_API_KEY_PERM_CACHE_PREFIX,
)


def build_key_namespace(env: str, service: str, module: str) -> str:
    return KEY_NAMESPACE_TEMPLATE.format(env=env, service=service, module=module)


def build_governed_key_prefix(prefix: str, *, env: str, service: str, module: str) -> str:
    namespace = build_key_namespace(env=env, service=service, module=module)
    return f"{namespace}{KEY_NAMESPACE_SEPARATOR}{prefix}"


def build_governed_key(
    prefix: str,
    *,
    env: str,
    service: str,
    module: str,
    parts: Iterable[str] = (),
) -> str:
    base_key = build_governed_key_prefix(prefix=prefix, env=env, service=service, module=module)
    suffix = [part for part in parts if part is not None and part != ""]
    if not suffix:
        return base_key
    return f"{base_key}{KEY_NAMESPACE_SEPARATOR}{KEY_NAMESPACE_SEPARATOR.join(suffix)}"


# 项目基准目录
BASE_DIR = Path(__file__).parent.parent.parent

# 案例目录
DEMO_DIR = BASE_DIR / "demo"

# 案例数据目录
DEMO_DATA = DEMO_DIR / "data"

# 项目源代码目录
PROJECT_DIR = BASE_DIR / "tools"

# 测试目录
TEST_DIR = BASE_DIR / "tests"

# 默认分页
DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 1000

# 时间格式
DEFAULT_TIME_FMT =  "%Y-%m-%d %H:%M:%S"
