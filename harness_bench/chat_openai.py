from __future__ import annotations

from typing import Any

import openai
from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatResult
from langchain_openai import ChatOpenAI

# Providers disagree on how a reasoning trace is named in a chat-completions
# message: vLLM / SGLang deployments emit `reasoning_content`, while OpenRouter
# style gateways emit `reasoning`. Capture both and echo each trace back under
# the key it arrived on — sending the wrong one is silently ignored.
REASONING_KEYS = ("reasoning_content", "reasoning")


class ReasoningAwareChatOpenAI(ChatOpenAI):
    """`ChatOpenAI` that keeps provider reasoning traces across agent turns.

    `ChatOpenAI` targets the official OpenAI schema and drops non-standard
    response fields, so a reasoning model driving an agent loop loses its own
    thoughts after every tool call. Captured traces are always stored on the
    `AIMessage`; they are only replayed to the model when
    `forward_reasoning_history` is set.
    """

    forward_reasoning_history: bool = False

    def _create_chat_result(
        self,
        response: dict | openai.BaseModel,
        generation_info: dict | None = None,
    ) -> ChatResult:
        result = super()._create_chat_result(response, generation_info)
        response_dict = response if isinstance(response, dict) else response.model_dump()
        choices = response_dict.get("choices") or []
        for choice, generation in zip(choices, result.generations, strict=False):
            if not isinstance(generation.message, AIMessage):
                continue
            message = choice.get("message") or {}
            for key in REASONING_KEYS:
                reasoning = message.get(key)
                if reasoning:
                    generation.message.additional_kwargs[key] = reasoning
        return result

    def _get_request_payload(
        self,
        input_: LanguageModelInput,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        payload_messages = payload.get("messages")
        if not self.forward_reasoning_history or not isinstance(payload_messages, list):
            return payload

        # `_get_request_payload` builds `messages` one-for-one from this same
        # list, so positional zip keeps assistant turns aligned.
        messages = self._convert_input(input_).to_messages()
        for message, payload_message in zip(messages, payload_messages, strict=False):
            if not isinstance(message, AIMessage):
                continue
            for key in REASONING_KEYS:
                reasoning = message.additional_kwargs.get(key)
                if reasoning:
                    payload_message[key] = reasoning
        return payload
