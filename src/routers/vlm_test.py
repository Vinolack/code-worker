#!/usr/bin/env python3
# -*- coding: UTF-8 -*-
'''
@File    ：v1.py
@Author  ：even_lin
@Date    ：2025/7/7 20:28 
@Desc     : {模块描述}
'''
from http import HTTPStatus
from typing import Dict, Any

from fastapi import Depends, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.api_models.vlm.protocol import ErrorResponse
# from src.api_models.vlm.protocol import ErrorResponse, ChatCompletionResponse
from src.routers.base import BaseAPIRouter
from src.services.vlm import VlmService

router = BaseAPIRouter()

async def validate_json_request(raw_request: Request):
    content_type = raw_request.headers.get("content-type", "").lower()
    media_type = content_type.split(";", maxsplit=1)[0]
    if media_type != "application/json":
        raise RequestValidationError(errors=[
            "Unsupported Media Type: Only 'application/json' is allowed"
        ])

class DictReq(BaseModel):
    pass
@router.post("/v1/chat/completions",
             dependencies=[Depends(validate_json_request)],
             responses={
                 HTTPStatus.OK.value: {
                     "content": {
                         "text/event-stream": {}
                     }
                 },
                 HTTPStatus.BAD_REQUEST.value: {
                     "model": ErrorResponse
                 },
                 HTTPStatus.NOT_FOUND.value: {
                     "model": ErrorResponse
                 },
                 HTTPStatus.INTERNAL_SERVER_ERROR.value: {
                     "model": ErrorResponse
                 }
             })

async def create_chat_completion(req:Dict[str, Any],
                                 request: Request):
    token = request.headers.get("Authorization") or ""
    token = token.replace("Bearer ", "")
    # todo 分割拿到api-key
    service = VlmService("api_key","http://36.140.65.192:8083/v1")

    stream = req.get("stream", False)
    generator = await service.chat_completions_create(req,stream)

    if isinstance(generator, ErrorResponse):
        return JSONResponse(content=generator.model_dump(),
                            status_code=generator.code)
    if not stream:
        return JSONResponse(content=generator.model_dump())

    return StreamingResponse(content=generator, media_type="text/event-stream")

