from typing import Annotated, Dict, Any

from fastapi import APIRouter, Request, Depends, UploadFile, File, Form

from src.base.exceptions import HttpException
from src.base.utils import web
from src.services.vlm import VlmService
from starlette.responses import StreamingResponse, JSONResponse, Response, FileResponse
from src.api_models.vlm.protocol import TranscriptionRequest
from src.utils.http_client import HttpErrorWithContent

router = APIRouter(prefix="")


async def get_api_key(request: Request) -> str:
    """
    从Authorization请求头中提取API Key。
    """
    api_key = request.headers.get("Authorization") or ""
    api_key = api_key.replace("Bearer ", "")
    return api_key

# @router.post("/v1/completions", summary="VLM Completions Proxy")
# async def completions(req:Dict[str, Any], api_key: str = Depends(get_api_key)，raw_request: Request):
#     """
#     代理所有到大模型的请求
#     其中包含记录调用日志逻辑
#     """
#     is_stream = req.get("stream", False)
#     if is_stream:
#         return StreamingResponse(
#             VlmService.stream_chat(req,api_key,"/v1/completions",raw_request),
#             media_type='text/event-stream'
#         )
#     else:
#         return JSONResponse(content=await VlmService.non_stream_chat(req,api_key,"/v1/completions"))

@router.post("/v1/chat/completions", summary="VLM Chat Completions Proxy")
async def chat_completions(req:Dict[str, Any],raw_request: Request, api_key: str = Depends(get_api_key)):
    """
    代理所有到大模型的请求
    其中包含记录调用日志逻辑
    """
    is_stream = req.get("stream", False)
    if is_stream:
        try:
            req_id, start_time = await VlmService.stream_chat_do_request(req, api_key, "/v1/chat/completions", raw_request)
            response = VlmService.stream_chat_get_response(req, api_key,"/v1/chat/completions",req_id, start_time, raw_request)
            return StreamingResponse(
                response,
                media_type='text/event-stream'
            )
        except HttpException as e:
            raise e
        except HttpErrorWithContent as e:
            raise e
        except Exception as e:
            raise HttpException(f"聊天请求失败: {str(e)}", "500")
    else:
        return JSONResponse(content=await VlmService.non_stream_chat(req,api_key,"/v1/chat/completions",raw_request))

@router.post("/v1/responses", summary="VLM Openai 聊天接口")
async def responses(req:Dict[str, Any],raw_request: Request, api_key: str = Depends(get_api_key)):
    """
    代理所有到大模型的请求
    其中包含记录调用日志逻辑
    """
    is_stream = req.get("stream", False)
    if is_stream:
        try:
            req_id, start_time = await VlmService.stream_responses_do_request(req, api_key, "/v1/responses", raw_request)
            response = VlmService.stream_responses_get_response(req, api_key,"/v1/responses",req_id, start_time, raw_request)
            return StreamingResponse(
                response,
                media_type='text/event-stream'
            )
        except HttpException as e:
            raise e
        except HttpErrorWithContent as e:
            raise e
        except Exception as e:
            raise HttpException(f"聊天请求失败: {str(e)}", "500")
    else:
        return JSONResponse(content=await VlmService.non_stream_responses(req,api_key,"/v1/responses",raw_request))

@router.post("/v1/responses/compact", summary="VLM Openai 聊天接口 Compact 版本")
async def responses_compact(req:Dict[str, Any],raw_request: Request, api_key: str = Depends(get_api_key)):
    """
    代理所有到大模型的请求 (Compact版本)
    如果模型使用transformer则直接返回400错误
    其中包含记录调用日志逻辑
    """
    # 检查模型是否使用transformer，如果是则返回400
    model_name = req.get("model")
    if model_name:
        try:
            is_transformer = await VlmService._check_responses_model_need_transform(model_name)
            if is_transformer:
                raise HttpException("该模型使用transformer转换，不支持compact API", "400")
        except HttpException as e:
            raise e
        except Exception as e:
            raise HttpException(f"检查模型配置失败: {str(e)}", "500")
    
    is_stream = req.get("stream", False)
    if is_stream:
        try:
            req_id, start_time = await VlmService.stream_responses_do_request(req, api_key, "/v1/responses/compact", raw_request)
            response = VlmService.stream_responses_get_response(req, api_key,"/v1/responses/compact",req_id, start_time, raw_request)
            return StreamingResponse(
                response,
                media_type='text/event-stream'
            )
        except HttpException as e:
            raise e
        except HttpErrorWithContent as e:
            raise e
        except Exception as e:
            raise HttpException(f"聊天请求失败: {str(e)}", "500")
    else:
        return JSONResponse(content=await VlmService.non_stream_responses(req,api_key,"/v1/responses/compact",raw_request))

@router.post("/v1/messages", summary="VLM Claude 聊天接口")
async def anthropic_messages(req:Dict[str, Any],raw_request: Request, api_key: str = Depends(get_api_key)):
    """
    代理所有到大模型的请求
    其中包含记录调用日志逻辑
    """
    is_stream = req.get("stream", False)
    if is_stream:
        try:
            req_id, start_time = await VlmService.anthropic_messages_stream_do_request(req, api_key, "/v1/messages", raw_request)
            response = VlmService.anthropic_messages_stream_get_response(req, api_key,"/v1/messages",req_id, start_time, raw_request)
            return StreamingResponse(
                response,
                media_type='text/event-stream'
            )
        except HttpException as e:
            raise e
        except HttpErrorWithContent as e:
            raise e
        except Exception as e:
            raise HttpException(f"聊天请求失败: {str(e)}", "500")
    else:
        return JSONResponse(content=await VlmService.non_stream_anthropic_messages(req,api_key,"/v1/messages",raw_request))

@router.post("/v1/messages/count_tokens", summary="VLM Claude 计算消息Token数接口")
async def count_message_tokens(req:Dict[str, Any],raw_request: Request, api_key: str = Depends(get_api_key)):
    """
    计算消息的Token数
    """
    try:
        token_count = await VlmService.anthropic_count_tokens(req, api_key, "/v1/messages/count_tokens", raw_request)
        return JSONResponse(content=token_count)
    except HttpException as e:
        raise e
    except HttpErrorWithContent as e:
        raise e
    except Exception as e:
        raise HttpException(f"计算Token数失败: {str(e)}", "500")
    
@router.post("/v1/embeddings", summary="VLM Embeddings Proxy")
async def embeddings(req:Dict[str, Any],raw_request: Request, api_key: str = Depends(get_api_key)):
    """
    代理所有到大模型的请求
    其中包含记录调用日志逻辑
    """
    return JSONResponse(content=await VlmService.proxy_request_non_stream(req,api_key, "/v1/embeddings",raw_request))


@router.post("/rerank", summary="VLM rerank Proxy")
async def rerank(req:Dict[str, Any],raw_request: Request, api_key: str = Depends(get_api_key)):
    """
    代理所有到大模型的请求
    其中包含记录调用日志逻辑
    """
    return JSONResponse(content=await VlmService.proxy_request_non_stream(req,api_key,"/rerank",raw_request))

@router.post("/v1/rerank", summary="VLM v1 rerank Proxy")
async def v1_rerank(req:Dict[str, Any],raw_request: Request, api_key: str = Depends(get_api_key)):
    """
    代理所有到大模型的请求
    其中包含记录调用日志逻辑
    """
    return JSONResponse(content=await VlmService.proxy_request_non_stream(req,api_key,"/v1/rerank",raw_request))

@router.post("/v2/rerank", summary="VLM v2 rerank Proxy")
async def v2_rerank(req:Dict[str, Any],raw_request: Request, api_key: str = Depends(get_api_key)):
    """
    代理所有到大模型的请求
    其中包含记录调用日志逻辑
    """
    return JSONResponse(content=await VlmService.proxy_request_non_stream(req,api_key,"/v2/rerank",raw_request))

@router.get("/v1/models", summary="models")
async def models():
    """
    models list
    """
    return JSONResponse(content=await VlmService.models())

@router.get("/v1/models/{model_id}", summary="get specific model")
async def get_model(model_id: str):
    """
    获取特定模型的详细信息
    """
    try:
        model_data = await VlmService.get_model(model_id)
        return JSONResponse(content=model_data)
    except Exception as e:
        raise HttpException(f"找不到 {model_id} 模型: {str(e)}", "404")

   

@router.post("/v1/audio/transcriptions", summary="VLM Audio Transcriptions Proxy")
async def audio_transcriptions(
    raw_request: Request,
    request: Annotated[TranscriptionRequest, Form()],
    api_key: str = Depends(get_api_key)
):
    """
    代理音频转录请求到大模型
    支持流式和非流式响应
    """
    await VlmService.audio_transcriptions_precheck_before_read(
        api_key_digest=api_key,
        requested_model=request.model,
    )

    if not request.stream:
        return JSONResponse(content=await VlmService.audio_transcriptions_non_stream(request, api_key, raw_request))

    filename = request.file.filename or "audio"
    content_type = request.file.content_type or "application/octet-stream"
    file_content = await request.file.read()
    try:
        req_id, start_time = await VlmService.audio_transcriptions_do_request(
            request,
            api_key,
            filename,
            content_type,
            file_content,
            raw_request,
        )
        response = VlmService.audio_transcriptions_get_response(req_id, start_time, raw_request)
        return StreamingResponse(
            response,
            media_type='text/event-stream'
        )
    except HttpException as e:
            raise e
    except HttpErrorWithContent as e:
        raise e
    except Exception as e:
        raise HttpException(f"音频转录请求失败: {str(e)}", "500")

@router.post("/v1/images/generations", summary="VLM Image Generations Proxy")
async def image_generations(req:Dict[str, Any],raw_request: Request, api_key: str = Depends(get_api_key)):
    """
    代理图像生成请求到大模型
    支持流式和非流式响应
    """
    is_stream = req.get("stream", False)
    if is_stream:
        try:
            req_id, start_time = await VlmService.image_generations_do_request(req, api_key, raw_request)
            response = VlmService.image_generations_get_response(req_id, start_time, raw_request)
            return StreamingResponse(
                response,
                media_type='text/event-stream'
            )
        except HttpException as e:
            raise e
        except HttpErrorWithContent as e:
            raise e
        except Exception as e:
            raise HttpException(f"图像生成请求失败: {str(e)}", "500")
    else:
        return JSONResponse(content=await VlmService.non_stream_image_generation(req,api_key,raw_request))
    
    
# TTS
@router.post("/audio/v1/tts", summary="VLM TTS Proxy")
async def tts(req:Dict[str, Any], raw_request: Request, api_key: str = Depends(get_api_key)):
    """
    代理TTS相关请求
    """
    try:
        content, filename, file_ext = await VlmService.proxy_tts(req, api_key, raw_request)
        content_type = "application/octet-stream"
        if file_ext == "wav":
            content_type = "audio/wav"
        elif file_ext == "mp3":
            content_type = "audio/mpeg"
        elif file_ext == "ogg":
            content_type = "audio/ogg"
        elif file_ext == "flac":
            content_type = "audio/flac"
        return Response(
            content=content,
            media_type=content_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}.{file_ext}"'}
        )
    except HttpException as e:
        raise e
    except HttpErrorWithContent as e:
        raise e
    except Exception as e:
        raise HttpException(f"TTS请求失败: {str(e)}", "500")