#!/usr/bin/env python3
# -*- coding: UTF-8 -*-
'''
@File    ：protocol.py
@Author  ：even_lin
@Date    ：2025/7/7 20:43
@Desc     : {模块描述}
'''
import uuid
from typing import ClassVar, Optional, Literal, Union, Any
from pydantic import (BaseModel, ConfigDict,Field)
import time
from fastapi import HTTPException, UploadFile
from typing_extensions import TypeAlias
from http import HTTPStatus

from pydantic import (BaseModel, ConfigDict, Field, TypeAdapter,
                      ValidationInfo, field_validator, model_validator)

class OpenAIBaseModel(BaseModel):
    # OpenAI API does allow extra fields
    model_config = ConfigDict(extra="allow")

    # Cache class field names
    field_names: ClassVar[Optional[set[str]]] = None
class ErrorResponse(OpenAIBaseModel):
    object: str = "error"
    message: str
    type: str
    param: Optional[str] = None
    code: int


## Protocols for Audio
AudioResponseFormat: TypeAlias = Literal["json", "text", "srt", "verbose_json",
                                         "vtt"]

class TranscriptionRequest(OpenAIBaseModel):
    # Ordered by official OpenAI API documentation
    # https://platform.openai.com/docs/api-reference/audio/createTranscription

    file: UploadFile
    """
    The audio file object (not file name) to transcribe, in one of these
    formats: flac, mp3, mp4, mpeg, mpga, m4a, ogg, wav, or webm.
    """

    model: Optional[str] = None
    """ID of the model to use.
    """

    language: Optional[str] = None
    """The language of the input audio.

    Supplying the input language in
    [ISO-639-1](https://en.wikipedia.org/wiki/List_of_ISO_639-1_codes) format
    will improve accuracy and latency.
    """

    prompt: str = Field(default="")
    """An optional text to guide the model's style or continue a previous audio
    segment.

    The [prompt](https://platform.openai.com/docs/guides/speech-to-text#prompting)
    should match the audio language.
    """

    response_format: AudioResponseFormat = Field(default="json")
    """
    The format of the output, in one of these options: `json`, `text`, `srt`,
    `verbose_json`, or `vtt`.
    """

    ## TODO (varun) : Support if set to 0, certain thresholds are met !!

    timestamp_granularities: list[Literal["word", "segment"]] = Field(
        alias="timestamp_granularities[]", default=[])
    """The timestamp granularities to populate for this transcription.

    `response_format` must be set `verbose_json` to use timestamp granularities.
    Either or both of these options are supported: `word`, or `segment`. Note:
    There is no additional latency for segment timestamps, but generating word
    timestamps incurs additional latency.
    """

    stream: Optional[bool] = False
    """When set, it will enable output to be streamed in a similar fashion
    as the Chat Completion endpoint.
    """
    # --8<-- [start:transcription-extra-params]
    # Flattened stream option to simplify form data.
    stream_include_usage: Optional[bool] = False
    stream_continuous_usage_stats: Optional[bool] = False

    vllm_xargs: Optional[dict[str, Union[str, int, float]]] = Field(
        default=None,
        description=("Additional request parameters with string or "
                     "numeric values, used by custom extensions."),
    )
    # --8<-- [end:transcription-extra-params]

    to_language: Optional[str] = None
    """The language of the output audio we transcribe to.

    Please note that this is not currently used by supported models at this
    time, but it is a placeholder for future use, matching translation api.
    """

    # --8<-- [start:transcription-sampling-params]
    temperature: float = Field(default=0.0)
    """The sampling temperature, between 0 and 1.

    Higher values like 0.8 will make the output more random, while lower values
    like 0.2 will make it more focused / deterministic. If set to 0, the model
    will use [log probability](https://en.wikipedia.org/wiki/Log_probability)
    to automatically increase the temperature until certain thresholds are hit.
    """

    top_p: Optional[float] = None
    """Enables nucleus (top-p) sampling, where tokens are selected from the
    smallest possible set whose cumulative probability exceeds `p`.
    """

    top_k: Optional[int] = None
    """Limits sampling to the `k` most probable tokens at each step."""

    min_p: Optional[float] = None
    """Filters out tokens with a probability lower than `min_p`, ensuring a
    minimum likelihood threshold during sampling.
    """

    seed: Optional[int] = Field(None)
    """The seed to use for sampling."""

    frequency_penalty: Optional[float] = 0.0
    """The frequency penalty to use for sampling."""

    repetition_penalty: Optional[float] = None
    """The repetition penalty to use for sampling."""

    presence_penalty: Optional[float] = 0.0
    """The presence penalty to use for sampling."""
    # --8<-- [end:transcription-sampling-params]

    # Default sampling parameters for transcription requests.
    _DEFAULT_SAMPLING_PARAMS: dict = {
        "repetition_penalty": 1.0,
        "temperature": 1.0,
        "top_p": 1.0,
        "top_k": 0,
        "min_p": 0.0,
    }

    # def to_sampling_params(
    #         self,
    #         default_max_tokens: int,
    #         default_sampling_params: Optional[dict] = None) -> SamplingParams:

    #     max_tokens = default_max_tokens

    #     if default_sampling_params is None:
    #         default_sampling_params = {}

    #     # Default parameters
    #     if (temperature := self.temperature) is None:
    #         temperature = default_sampling_params.get(
    #             "temperature", self._DEFAULT_SAMPLING_PARAMS["temperature"])
    #     if (top_p := self.top_p) is None:
    #         top_p = default_sampling_params.get(
    #             "top_p", self._DEFAULT_SAMPLING_PARAMS["top_p"])
    #     if (top_k := self.top_k) is None:
    #         top_k = default_sampling_params.get(
    #             "top_k", self._DEFAULT_SAMPLING_PARAMS["top_k"])
    #     if (min_p := self.min_p) is None:
    #         min_p = default_sampling_params.get(
    #             "min_p", self._DEFAULT_SAMPLING_PARAMS["min_p"])

    #     if (repetition_penalty := self.repetition_penalty) is None:
    #         repetition_penalty = default_sampling_params.get(
    #             "repetition_penalty",
    #             self._DEFAULT_SAMPLING_PARAMS["repetition_penalty"])

    #     return SamplingParams.from_optional(temperature=temperature,
    #                                         max_tokens=max_tokens,
    #                                         seed=self.seed,
    #                                         top_p=top_p,
    #                                         top_k=top_k,
    #                                         min_p=min_p,
    #                                         frequency_penalty=self.frequency_penalty,
    #                                         repetition_penalty=repetition_penalty,
    #                                         presence_penalty=self.presence_penalty,
    #                                         output_kind=RequestOutputKind.DELTA
    #                                         if self.stream \
    #                                         else RequestOutputKind.FINAL_ONLY,
    #                                         extra_args=self.vllm_xargs)

    @model_validator(mode="before")
    @classmethod
    def validate_transcription_request(cls, data):
        if isinstance(data.get("file"), str):
            raise HTTPException(
                status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                detail="Expected 'file' to be a file-like object, not 'str'.",
            )

        stream_opts = ["stream_include_usage", "stream_continuous_usage_stats"]
        stream = data.get("stream", False)
        if any(bool(data.get(so, False)) for so in stream_opts) and not stream:
            raise ValueError(
                "Stream options can only be defined when `stream=True`.")

        return data



# def random_tool_call_id() -> str:
#     return f"chatcmpl-tool-{random_uuid()}"
#
# class Logprob:
#     """Infos for supporting OpenAI compatible logprobs and token ranks.
#
#     Attributes:
#         logprob: The logprob of chosen token
#         rank: The vocab rank of chosen token (>=1)
#         decoded_token: The decoded chosen token index
#     """
#     logprob: float
#     rank: Optional[int] = None
#     decoded_token: Optional[str] = None
#
#
# # {token_id -> logprob} per each sequence group. None if the corresponding
# # sequence group doesn't require prompt logprob.
# PromptLogprobs = list[Optional[dict[int, Logprob]]]
# # {token_id -> logprob} for each sequence group.
# SampleLogprobs = list[dict[int, Logprob]]
#
#
#
# class PromptTokenUsageInfo(OpenAIBaseModel):
#     cached_tokens: Optional[int] = None
#
# class UsageInfo(OpenAIBaseModel):
#     prompt_tokens: int = 0
#     total_tokens: int = 0
#     completion_tokens: Optional[int] = 0
#     prompt_tokens_details: Optional[PromptTokenUsageInfo] = None
#
# class FunctionCall(OpenAIBaseModel):
#     name: str
#     arguments: str
#
# class ToolCall(OpenAIBaseModel):
#     id: str = Field(default_factory=random_tool_call_id)
#     type: Literal["function"] = "function"
#     function: FunctionCall
#
# class ChatMessage(OpenAIBaseModel):
#     role: str
#     reasoning_content: Optional[str] = None
#     content: Optional[str] = None
#     tool_calls: list[ToolCall] = Field(default_factory=list)
#
# class ChatCompletionLogProb(OpenAIBaseModel):
#     token: str
#     logprob: float = -9999.0
#     bytes: Optional[list[int]] = None
#
#
# class ChatCompletionLogProbsContent(ChatCompletionLogProb):
#     # Workaround: redefine fields name cache so that it's not
#     # shared with the super class.
#     field_names: ClassVar[Optional[set[str]]] = None
#     top_logprobs: list[ChatCompletionLogProb] = Field(default_factory=list)
#
#
# class ChatCompletionLogProbs(OpenAIBaseModel):
#     content: Optional[list[ChatCompletionLogProbsContent]] = None
#
# class ChatCompletionResponseChoice(OpenAIBaseModel):
#     index: int
#     message: ChatMessage
#     logprobs: Optional[ChatCompletionLogProbs] = None
#     # per OpenAI spec this is the default
#     finish_reason: Optional[str] = "stop"
#     # not part of the OpenAI spec but included in vLLM for legacy reasons
#     stop_reason: Optional[Union[int, str]] = None
#
# class ChatCompletionResponse(OpenAIBaseModel):
#     id: str = Field(default_factory=lambda: f"chatcmpl-{random_uuid()}")
#     object: Literal["chat.completion"] = "chat.completion"
#     created: int = Field(default_factory=lambda: int(time.time()))
#     model: str
#     choices: list[ChatCompletionResponseChoice]
#     usage: UsageInfo
#     prompt_logprobs: Optional[list[Optional[dict[int, Logprob]]]] = None
#     kv_transfer_params: Optional[dict[str, Any]] = Field(
#         default=None, description="KVTransfer parameters.")
#
#
# def random_uuid() -> str:
#     return str(uuid.uuid4().hex)