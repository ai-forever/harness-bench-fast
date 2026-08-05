from __future__ import annotations

from typing import Any

import openai
from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatResult
from langchain_openai import ChatOpenAI


class ReasoningAwareChatOpenAI(ChatOpenAI):
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
            reasoning_content = choice.get("message", {}).get("reasoning_content")
            if reasoning_content is not None and isinstance(generation.message, AIMessage):
                generation.message.additional_kwargs["reasoning_content"] = reasoning_content
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

        messages = self._convert_input(input_).to_messages()
        for message, payload_message in zip(messages, payload_messages, strict=False):
            if not isinstance(message, AIMessage):
                continue
            reasoning_content = message.additional_kwargs.get("reasoning_content")
            if reasoning_content is not None:
                payload_message["reasoning_content"] = reasoning_content
        return payload
