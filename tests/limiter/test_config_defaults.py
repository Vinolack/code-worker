import importlib
from unittest.mock import mock_open, patch


def _load_config_module(fake_config: dict):
    module_name = "src.config.config"
    with patch("os.path.exists", return_value=True), patch("builtins.open", mock_open(read_data="")), patch(
        "toml.load", return_value=fake_config
    ):
        module = importlib.import_module(module_name)
        return importlib.reload(module)


def test_shared_limiter_redis_target():
    fake_config = {
        "server_name": "worker-a",
        "listen_host": "0.0.0.0",
        "listen_port": 8084,
        "frontend_index_url": "http://localhost:5173",
        "allow_origins": ["*"],
        "mysql": {
            "host": "127.0.0.1",
            "port": 3306,
            "user": "user",
            "password": "pwd",
            "database": "db",
        },
        "redis": {
            "host": "10.10.10.10",
            "port": 6381,
            "password": "redis-pwd",
            "db": 5,
        },
        "auth_whitelist_urls": [],
        "admin_urls": [],
        "vlm_proxy_urls": [],
        "sync_ai_model": {"sync_url": "http://127.0.0.1/sync"},
        "internal_service": {"secret_key": "internal-secret"},
        "worker": {"encrypt_key": "worker-key"},
        "service_check": {"magic_secret": "magic"},
    }

    config = _load_config_module(fake_config)

    assert config.LIMITER_REDIS_HOST == config.REDIS_HOST
    assert config.LIMITER_REDIS_PORT == config.REDIS_PORT
    assert config.LIMITER_REDIS_PASSWORD == config.REDIS_PASSWORD
    assert config.LIMITER_REDIS_DB == config.REDIS_DB
    assert config.LIMITER_REDIS_PREFIX == "xaio:limiter"
    assert config.LIMITER_REDIS_ENV == "prod"
    assert config.LIMITER_REDIS_SERVICE == config.SERVER_NAME
    assert config.LEGACY_REDIS_ENV == config.LIMITER_REDIS_ENV
    assert config.LEGACY_REDIS_SERVICE == config.LIMITER_REDIS_SERVICE
    assert config.LIMITER_ROLLOUT_PERCENT == 100
    assert config.SCHEDULER_SIGNAL_KEY == f"{config.SERVER_NAME}:scheduler:online"
    assert config.SCHEDULER_SIGNAL_TTL_SECONDS == 30
    assert config.SCHEDULER_SIGNAL_REFRESH_INTERVAL_SECONDS == 10
    assert config.SCHEDULER_SIGNAL_WAIT_TIMEOUT_SECONDS == 60
    assert config.SCHEDULER_SIGNAL_WAIT_POLL_INTERVAL_SECONDS == 1.0
