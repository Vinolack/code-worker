from src.routers import chat_app
from src.routers import vlm
from src.routers import health
from src.routers.base import BaseAPIRouter

api_router = BaseAPIRouter()

api_router.include_router(health.router, tags=["健康检查"])
api_router.include_router(chat_app.router, tags=["ai对话模块"])
api_router.include_router(vlm.router, tags=["vlm"])