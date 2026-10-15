"""Scripted chat model for tests and the built-in demo run: no network, deterministic."""

from collections.abc import Sequence
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class ScriptedChatModel(BaseChatModel):
    """Replays a fixed list of AIMessages (tool calls included), then repeats the last one."""

    script: list[AIMessage]
    cursor: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> ScriptedChatModel:
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        message = self.script[min(self.cursor, len(self.script) - 1)]
        self.cursor += 1
        message = message.model_copy(
            update={
                "usage_metadata": {
                    "input_tokens": sum(len(str(m.content)) for m in messages) // 4 + 1,
                    "output_tokens": len(str(message.content)) // 4 + 1,
                    "total_tokens": 0,
                }
            }
        )
        return ChatResult(generations=[ChatGeneration(message=message)])


class RuleChatModel(BaseChatModel):
    """Structured-output model driven by a function of the prompt text, for deterministic tests.

    `handler(prompt)` returns the arguments of the tool call (the structured result); it sees the
    same text a real model would, so concurrency and ordering do not matter."""

    handler: Any
    tool_name: str = "ExtractionResult"
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "rule"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> RuleChatModel:
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.calls += 1
        prompt = "\n".join(str(m.content) for m in messages)
        args = self.handler(prompt)
        message = AIMessage(
            content="",
            tool_calls=[
                {"name": self.tool_name, "args": args, "id": f"call-{self.calls}"}
            ],
            usage_metadata={
                "input_tokens": len(prompt) // 4 + 1,
                "output_tokens": 50,
                "total_tokens": 0,
            },
        )
        return ChatResult(generations=[ChatGeneration(message=message)])
