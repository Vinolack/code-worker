from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.base.logging import logger
from src.base.logging import setup_logging
from src.base.redis import BaseRedisManager
from src.base.utils import TraceUtil
from src.config import config
from src.jobs.scheduler import scheduler_manager
from src.middlewares import register_middlewares
from src.middlewares.depends import register_depends
from src.middlewares.error_handler import register_exception_handler
from src.routers import api_router
from src.services.api_key import ApiKeyService
from src.services.vlm import VlmService


@asynccontextmanager
async def lifespan(app: FastAPI):
    await startup()
    yield
    await shutdown()


app = FastAPI(
    description="ys-vxai系统",
    lifespan=lifespan,
    dependencies=register_depends(),  # 注册全局依赖
    middleware=register_middlewares(),  # 注册web中间件
    exception_handlers=register_exception_handler(),  # 注册web错误处理
)
app.include_router(api_router)


async def init_setup():
    """初始化项目配置"""

    setup_logger()
    init_redis()


async def startup():
    """项目启动时准备环境"""
    TraceUtil.set_trace_id(title="app-server")

    await init_setup()
    await VlmService.startup()

    logger.info("fastapi startup success")


async def shutdown():
    await VlmService.shutdown()
    await ApiKeyService.close_shared_client()
    logger.error("app shutdown")


async def startup_scheduler_service():
    TraceUtil.set_trace_id(title="app-scheduler")
    await init_setup()
    await scheduler_manager.start()
    logger.info("scheduler startup success")


async def shutdown_scheduler_service():
    await scheduler_manager.shutdown()
    logger.error("scheduler shutdown")


def init_redis():
    BaseRedisManager.init_redis_client(
        async_client=True,
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        password=config.REDIS_PASSWORD,
        db=config.REDIS_DB,
        max_connections=config.REDIS_MAX_CONNECTIONS,
    )



def _logger_filter(record):
    """日志过滤器补充request_id或trace_id"""
    req_id = TraceUtil.get_req_id()
    trace_id = TraceUtil.get_trace_id()

    trace_msg = f"{req_id} | {trace_id}"
    record["trace_msg"] = trace_msg
    return record


def setup_logger():
    """配置项目日志信息"""
    setup_logging(
        log_dir=config.LOGGING_DIR,
        log_filter=_logger_filter,
        log_format=config.LOG_FORMAT,
        console_log_level=config.CONSOLE_LOG_LEVEL,
        log_retention=config.SERVER_LOGGING_RETENTION,
        log_rotation=config.SERVER_LOGGING_ROTATION,
    )