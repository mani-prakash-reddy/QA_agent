"""Single entry point for chat and embedding models. Every call is secret-scanned and metered."""

from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import LLMResult
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from app.core.config import settings
from app.qa.security.scanner import SecretScanner

ModelKind = Literal[
    "supervisor", "explorer", "test_author", "mapper", "critic", "extractor"
]

# Strong model for planning/authoring/extraction, fast model for high-volume steps.
_FAST_KINDS: set[str] = {"explorer", "critic"}


class LLMConfigError(Exception):
    pass


class SecretGuard(BaseCallbackHandler):
    """Aborts a model call if any outgoing message contains a known secret."""

    raise_error = True
    run_inline = True

    def __init__(self, scanner: SecretScanner) -> None:
        self.scanner = scanner

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        **kwargs: Any,
    ) -> None:
        for batch in messages:
            for message in batch:
                self.scanner.assert_clean(str(message.content), "outgoing LLM message")
                for call in getattr(message, "tool_calls", None) or []:
                    self.scanner.assert_clean(str(call), "outgoing LLM tool call")

    def on_llm_start(
        self, serialized: dict[str, Any], prompts: list[str], **kwargs: Any
    ) -> None:
        for prompt in prompts:
            self.scanner.assert_clean(prompt, "outgoing LLM prompt")


@dataclass
class UsageTotals:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None  # None when the provider does not report cost


@dataclass
class UsageMeter(BaseCallbackHandler):
    """Accumulates token usage; optional `on_usage` hook feeds the event stream."""

    totals: UsageTotals = field(default_factory=UsageTotals)
    on_usage: Any = None  # callable(input_tokens, output_tokens, model) -> None

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        for generations in response.generations:
            for gen in generations:
                usage = getattr(getattr(gen, "message", None), "usage_metadata", None)
                if not usage:
                    continue
                self.totals.calls += 1
                self.totals.input_tokens += usage.get("input_tokens", 0)
                self.totals.output_tokens += usage.get("output_tokens", 0)
                if self.on_usage:
                    self.on_usage(
                        usage.get("input_tokens", 0), usage.get("output_tokens", 0)
                    )


def model_name_for(kind: ModelKind, overrides: dict[str, str] | None = None) -> str:
    if overrides and kind in overrides:
        return overrides[kind]
    return settings.LLM_MODEL_FAST if kind in _FAST_KINDS else settings.LLM_MODEL


def _check_confidential(model: str, confidential: bool) -> None:
    if confidential and model.endswith(":free"):
        raise LLMConfigError(
            f"model '{model}' is a free-tier model and cannot be used for a confidential project"
        )


def get_chat_model(
    kind: ModelKind,
    *,
    overrides: dict[str, str] | None = None,
    confidential: bool = False,
    scanner: SecretScanner | None = None,
    meter: UsageMeter | None = None,
    temperature: float = 0.0,
) -> BaseChatModel:
    if not settings.LLM_BASE_URL or settings.LLM_API_KEY is None:
        raise LLMConfigError("LLM_BASE_URL / LLM_API_KEY are not configured")
    model = model_name_for(kind, overrides)
    _check_confidential(model, confidential)
    callbacks: list[BaseCallbackHandler] = []
    if scanner is not None:
        callbacks.append(SecretGuard(scanner))
    if meter is not None:
        callbacks.append(meter)
    return ChatOpenAI(
        model=model,
        base_url=settings.LLM_BASE_URL,
        api_key=settings.LLM_API_KEY,
        temperature=temperature,
        max_retries=3,  # the client backs off on 429/5xx
        timeout=120,
        callbacks=callbacks,
    )


def get_embeddings(*, confidential: bool = False) -> OpenAIEmbeddings:
    if not settings.EMBEDDING_BASE_URL or settings.EMBEDDING_API_KEY is None:
        raise LLMConfigError(
            "EMBEDDING_BASE_URL / EMBEDDING_API_KEY are not configured"
        )
    _check_confidential(settings.EMBEDDING_MODEL, confidential)
    return OpenAIEmbeddings(
        model=settings.EMBEDDING_MODEL,
        base_url=settings.EMBEDDING_BASE_URL,
        api_key=settings.EMBEDDING_API_KEY,
        # Non-OpenAI endpoints expect raw strings, not tiktoken token ids.
        check_embedding_ctx_length=False,
    )
