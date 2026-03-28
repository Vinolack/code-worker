#!/usr/bin/env python3
# -*- coding: UTF-8 -*-
'''
@File    ：chat_app.py
@Author  ：even_lin
@Date    ：2025/6/10 13:55
@Desc     : {模块描述}
'''
import json
from fastapi import Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from src.base.logging import logger
from src.api_models.chat_app.req_model import ChatReq
from src.api_models.comm.CommResp import R
from src.base.constants.const import USER_AI_MODEL_SET_PREFIX
from src.base.exceptions.base import HttpException
from src.dao.redis import RedisManager
from src.routers.base import BaseAPIRouter
from src.routers.vlm import get_api_key
from src.services.chat_app import ChatAppService
from src.services.vlm import VlmService
from src.utils.http_client import HttpErrorWithContent
router = BaseAPIRouter(prefix="/chat_app")

@router.post("/chat")
async def chat(req: ChatReq,raw_request: Request,api_key: str = Depends(get_api_key)):
    # if req.stream:
    #     return StreamingResponse(
    #         ChatAppService().stream_chat(req,api_key),
    #         media_type='text/event-stream'
    #     )
    # else:
    #     return R.ok(await ChatAppService().non_stream_chat(req,api_key))
    is_stream = req.stream
    req_dict = req.model_dump()
    if is_stream:
        try:
            req_id, start_time = await VlmService.stream_chat_do_request(req_dict, api_key, "/v1/chat/completions", raw_request)
            response = VlmService.stream_chat_get_response(req_dict, api_key,"/v1/chat/completions",req_id, start_time, raw_request)
            return StreamingResponse(
                response,
                media_type='text/event-stream'
            )
        except HttpErrorWithContent as e:
            raise
        except HttpException as e:
            raise
        except Exception as e:
            raise HttpException(f"聊天请求失败: {str(e)}", "500")
    else:
        return JSONResponse(content=await VlmService.non_stream_chat(req_dict,api_key,"/v1/chat/completions",raw_request))