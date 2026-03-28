import logging
import os
import sys
from datetime import timedelta
from typing import Any

import toml

# --- Configuration Loading ---
_APP_CONFIG: Any = {}
CONFIG_FILE_NAME = 'config.toml'
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_FILE_PATH = os.path.join(_project_root, CONFIG_FILE_NAME)
EXAMPLE_CONFIG_FILE_PATH = os.path.join(_project_root, 'config.example.toml')

try:
    if os.path.exists(CONFIG_FILE_PATH):
        with open(CONFIG_FILE_PATH, 'r', encoding='utf-8') as f:
            _APP_CONFIG = toml.load(f)
    elif os.path.exists(EXAMPLE_CONFIG_FILE_PATH):
        print(f"WARNING: {CONFIG_FILE_PATH} not found. Loading {EXAMPLE_CONFIG_FILE_PATH} as a fallback.", file=sys.stderr)
        with open(EXAMPLE_CONFIG_FILE_PATH, 'r', encoding='utf-8') as f:
            _APP_CONFIG = toml.load(f)
    else:
        # Critical error if no config can be loaded
        print(f"Error: Configuration file {CONFIG_FILE_PATH} and fallback {EXAMPLE_CONFIG_FILE_PATH} not found.", file=sys.stderr)
        sys.exit(1)
except Exception as e:
    print(f"Error: loading configuration: {e}", file=sys.stderr)
    sys.exit(1)

def _get_config_value(path: str, default: Any = None) -> Any:
    keys = path.split('.')
    value: Any = _APP_CONFIG
    try:
        for key in keys:
            value = value[key]
        if value is None:
            raise KeyError
        return value
    except (KeyError, TypeError): # Handles missing keys or non-dict objects in path
        if default is not None:
            print(f"WARNING: Config key '{path}' not found, using default: {default}", file=sys.stderr)
            return default
        else:
            print(f"Error: Config key '{path}' not found", file=sys.stderr)
            raise KeyError


def _get_optional_config_value(path: str) -> Any:
    keys = path.split('.')
    value: Any = _APP_CONFIG
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def _get_optional_bool_value(path: str, default: bool = False) -> bool:
    value = _get_optional_config_value(path)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default

# --- Server Settings ---
SERVER_NAME = _get_config_value('server_name', 'xaio-code-worker')
SERVER_HOST = _get_config_value('listen_host')
SERVER_PORT = int(_get_config_value('listen_port')) # Ensure port is int
_server_log_level_str = _get_config_value('log_level', 'WARNING').upper()
SERVER_LOG_LEVEL = getattr(logging, _server_log_level_str, logging.WARNING)
SERVER_ACCESS_LOG = _get_config_value('access_log', False)
WORKER_NUM = int(_get_config_value('workers',1))
FRONTEND_INDEX_URL = _get_config_value('frontend_index_url')
ALLOW_ORIGINS = _get_config_value('allow_origins')


# MySQL
MYSQL_HOST = _get_config_value('mysql.host')
MYSQL_PORT = int(_get_config_value('mysql.port'))
MYSQL_USER = _get_config_value('mysql.user')
MYSQL_PASSWORD = _get_config_value('mysql.password')
MYSQL_DBNAME = _get_config_value('mysql.database')
MYSQL_SQL_ECHO = _get_config_value('mysql.sql_echo', False)
MYSQL_POOL_SIZE = _get_config_value('mysql.pool_size', 20)

# Redis
REDIS_HOST = _get_config_value('redis.host', '127.0.0.1')
REDIS_PORT = int(_get_config_value('redis.port', 6379))
REDIS_PASSWORD = _get_config_value('redis.password', '')
REDIS_DB = int(_get_config_value('redis.db', 0))
REDIS_MAX_CONNECTIONS = _get_config_value('redis.max_connections', 100)

LIMITER_REDIS_HOST = _get_config_value('limiter.redis.host', REDIS_HOST)
LIMITER_REDIS_PORT = int(_get_config_value('limiter.redis.port', REDIS_PORT))
LIMITER_REDIS_PASSWORD = _get_config_value('limiter.redis.password', REDIS_PASSWORD)
LIMITER_REDIS_DB = int(_get_config_value('limiter.redis.db', REDIS_DB))
LIMITER_REDIS_PREFIX = _get_config_value('limiter.redis.prefix', 'xaio:limiter')
LIMITER_REDIS_ENV = _get_config_value('limiter.redis.env', 'prod')
LIMITER_REDIS_SERVICE = _get_config_value('limiter.redis.service', SERVER_NAME)
LEGACY_REDIS_ENV = str(
    _get_optional_config_value('legacy.redis.env')
    or _get_optional_config_value('limiter.redis.env')
    or LIMITER_REDIS_ENV
).strip()
LEGACY_REDIS_SERVICE = str(
    _get_optional_config_value('legacy.redis.service')
    or _get_optional_config_value('limiter.redis.service')
    or LIMITER_REDIS_SERVICE
).strip()

LIMITER_MODE = str(
    _get_optional_config_value('limiter.mode')
    or _get_optional_config_value('limiter.runtime.mode')
    or _get_optional_config_value('limiter.runtime_mode')
    or 'enforce'
).strip().lower()
LIMITER_FAIL_POLICY = str(
    _get_optional_config_value('limiter.fail_policy')
    or _get_optional_config_value('limiter.failure_policy')
    or _get_optional_config_value('limiter.redis_failure_policy')
    or 'fail-open'
).strip().lower()
LIMITER_LEASE_TTL_MS = int(
    _get_optional_config_value('limiter.lease_ttl_ms')
    or _get_optional_config_value('limiter.lease.ttl_ms')
    or 900_000
)
LIMITER_USER_TOTAL_CONCURRENCY_LIMIT = _get_optional_config_value('limiter.user_total_concurrency_limit')
LIMITER_USER_MODEL_CONCURRENCY_LIMIT = _get_optional_config_value('limiter.user_model_concurrency_limit')
LIMITER_SUBJECT_USE_USER_ID = _get_optional_bool_value('limiter.subject_use_user_id', default=False)
LIMITER_ENABLE_DYNAMIC_LIMITS = _get_optional_bool_value('limiter.enable_dynamic_limits', default=False)
LIMITER_ROLLOUT_PERCENT = int(
    _get_optional_config_value('limiter.rollout_percent')
    or _get_optional_config_value('limiter.runtime.rollout_percent')
    or _get_optional_config_value('limiter.runtime_rollout_percent')
    or 100
)

# --- Auth Settings ---
AUTH_WHITELIST_URLS = tuple(_get_config_value('auth_whitelist_urls', [])) # Ensure tuple
ADMIN_URLS = tuple(_get_config_value('admin_urls', [])) # Ensure tuple
VLM_PROXY_URLS = tuple(_get_config_value('vlm_proxy_urls', [])) # Ensure tuple

# --- Logging Settings ---
_logging_dir_relative = _get_config_value('logging.dir', 'logs/')
if not os.path.isabs(_logging_dir_relative):
    LOGGING_DIR = os.path.join(_project_root, _logging_dir_relative)
else:
    LOGGING_DIR = _logging_dir_relative

_console_log_level_str = _get_config_value('logging.console_log_level', 'WARNING').upper()
CONSOLE_LOG_LEVEL = getattr(logging, _console_log_level_str, logging.WARNING)
LOG_FORMAT = _get_config_value('logging.format', '{time} {level} {message}')
SERVER_LOGGING_ROTATION = _get_config_value('logging.server_rotation', '00:00')
ERROR_LOGGING_ROTATION = _get_config_value('logging.error_rotation', '10 MB')
SERVER_LOGGING_RETENTION = _get_config_value('logging.server_retention', '7 days')
ERROR_LOGGING_RETENTION = _get_config_value('logging.error_retention', '30 days')


# --- Chat Log Reporter Settings ---
CHAT_LOG_ENABLE = _get_config_value('chat_log_reporter.enable', True)
CHAT_LOG_REPORT_URL = _get_config_value('chat_log_reporter.report_url', 'http://127.0.0.1:8001/log/receive')
CHAT_LOG_REPORT_INTERVAL = int(_get_config_value('chat_log_reporter.report_interval_seconds', 60))
CHAT_LOG_BATCH_SIZE = int(_get_config_value('chat_log_reporter.batch_size', 100))
CHAT_LOG_RETRY_REPORT_INTERVAL = int(_get_config_value('chat_log_reporter.retry_report_interval_seconds', 60))
CHAT_LOG_RETRY_BATCH_SIZE = int(_get_config_value('chat_log_reporter.retry_batch_size', 100))

# --- Except Log Reporter Settings ---
EXCEPT_LOG_ENABLE = _get_config_value('except_log_reporter.enable', True)
EXCEPT_LOG_REPORT_URL = _get_config_value('except_log_reporter.report_url', 'http://127.0.0.1:8001/except_request_log/bulk_add')
EXCEPT_LOG_REPORT_INTERVAL = int(_get_config_value('except_log_reporter.report_interval_seconds', 120))
EXCEPT_LOG_BATCH_SIZE = int(_get_config_value('except_log_reporter.batch_size', 500))

# --- API Key Sync Settings ---
SYNC_API_KEYS_URL = _get_config_value('sync_api_keys.sync_url', 'http://127.0.0.1:8001/api_keys/valid_api_keys')
SYNC_API_KEYS_URL_V2 = _get_config_value('sync_api_keys.sync_url_v2', 'http://127.0.0.1:8001/api_keys/valid_api_keys_v2')
SYNC_API_KEYS_INTERVAL = int(_get_config_value('sync_api_keys.sync_interval_seconds', 60))

# --- BACKEND_URL ---
BACKEND_URL = _get_config_value(
    'backend_url',
    'http://127.0.0.1:8099'
)

# --- AI Model Sync Settings ---
SYNC_AI_MODEL_URL = _get_config_value('sync_ai_model.sync_url')
SYNC_AI_MODEL_INTERVAL = int(_get_config_value('sync_ai_model.sync_interval_seconds', 60))
SUPPORT_API_TYPES = tuple(_get_config_value('sync_ai_model.support_api_types', '').split(',')) # Ensure tuple

# --- Scheduler Settings ---
SCHEDULER_SIGNAL_KEY = str(
    _get_optional_config_value('scheduler.signal_key')
    or f'{SERVER_NAME}:scheduler:online'
).strip()
SCHEDULER_SIGNAL_TTL_SECONDS = int(
    _get_optional_config_value('scheduler.signal_ttl_seconds')
    or 30
)
SCHEDULER_SIGNAL_REFRESH_INTERVAL_SECONDS = int(
    _get_optional_config_value('scheduler.signal_refresh_interval_seconds')
    or max(1, SCHEDULER_SIGNAL_TTL_SECONDS // 3)
)
SCHEDULER_SIGNAL_WAIT_TIMEOUT_SECONDS = int(
    _get_optional_config_value('scheduler.wait_timeout_seconds')
    or 60
)
SCHEDULER_SIGNAL_WAIT_POLL_INTERVAL_SECONDS = float(
    _get_optional_config_value('scheduler.wait_poll_interval_seconds')
    or 1.0
)

# -- internal service --
INTERNAL_SERVICE_SECRET_KEY = _get_config_value('internal_service.secret_key')

# worker
WORKER_ENCRYPT_KEY = _get_config_value('worker.encrypt_key')

# service check
SERVICE_CHECK_MAGIC_SECRET = _get_config_value('service_check.magic_secret')

# vlm proxy
VLM_PROXY_RETRIES = int(_get_config_value('vlm_proxy.retries', 3))
VLM_PROXY_RETRY_INTERVAL = int(_get_config_value('vlm_proxy.retry_interval', 1))
VLM_PROXY_TOTAL_REQUEST_TIMEOUT = int(_get_config_value('vlm_proxy.total_request_timeout', 3600))
ANTHROPIC_BETA_HEADER_BLACKLIST = tuple(filter(None, _get_config_value('vlm_proxy.anthropic_beta_header_blacklist', '').split(',')))
