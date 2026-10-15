"""ToolGate: the only path from an agent to any capability. Enforces authorization, budgets, control flags,
secret redaction and event emission. Safety rules live here, never in prompts."""

import json
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ValidationError

from app.qa.agents.budget import BudgetTracker
from app.qa.agents.spec import AgentSpec
from app.qa.events import EventPublisher
from app.qa.runs.control import RunControl
from app.qa.security.scanner import SecretScanner

MAX_RESULT_CHARS = 8000


class ToolBlocked(Exception):
    """Raised by a tool implementation when safe mode forbids the action."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class AgentContext:
    run_id: str
    agent_id: str
    spec: AgentSpec
    budget: BudgetTracker
    events: EventPublisher
    control: RunControl
    scanner: SecretScanner
    parent_id: str | None = None
    # Process-local capabilities (browser session, db factory, ...). Never shown to the LLM.
    resources: dict[str, Any] = field(default_factory=dict)


ToolImpl = Callable[[AgentContext, Any], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    args_schema: type[BaseModel]
    impl: ToolImpl


class ToolGate:
    def __init__(self, tools: Iterable[ToolDef]) -> None:
        self._tools = {t.name: t for t in tools}

    def has(self, name: str) -> bool:
        return name in self._tools

    async def invoke(
        self, ctx: AgentContext, name: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        tool = self._tools.get(name)
        if tool is None or name not in ctx.spec.tools:
            ctx.budget.step()  # probing for tools is not free
            await ctx.events.emit(
                "blocked", agent_id=ctx.agent_id, reason="unauthorized_tool", tool=name
            )
            return {"error": f"tool '{name}' is not available to this agent"}

        # StopRequested / BudgetExceeded propagate and end the agent.
        await ctx.control.checkpoint(ctx.agent_id)
        ctx.budget.step()

        try:
            parsed = tool.args_schema.model_validate(args)
        except ValidationError as exc:
            return {"error": f"invalid arguments: {exc.errors(include_url=False)}"}

        await ctx.events.emit("tool_call", agent_id=ctx.agent_id, tool=name, args=args)
        try:
            result = await tool.impl(ctx, parsed)
        except ToolBlocked as blocked:
            await ctx.events.emit(
                "blocked", agent_id=ctx.agent_id, reason=blocked.reason, tool=name
            )
            return {"blocked": True, "reason": blocked.reason}
        except Exception as exc:  # tool failures are data for the agent, not crashes
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            await ctx.events.emit(
                "error", agent_id=ctx.agent_id, tool=name, message=str(exc)[:500]
            )
            return {"error": str(exc)[:500]}

        result = self._sanitize(ctx.scanner, result)
        await ctx.events.emit(
            "tool_result",
            agent_id=ctx.agent_id,
            tool=name,
            summary=json.dumps(result, default=str)[:500],
        )
        return result

    @staticmethod
    def _sanitize(scanner: SecretScanner, result: dict[str, Any]) -> dict[str, Any]:
        text = scanner.redact(json.dumps(result, default=str))
        if len(text) > MAX_RESULT_CHARS:
            return {"truncated": True, "content": text[:MAX_RESULT_CHARS]}
        loaded: dict[str, Any] = json.loads(text)
        return loaded

    def langchain_tools(self, ctx: AgentContext) -> list[StructuredTool]:
        tools: list[StructuredTool] = []
        for name in ctx.spec.tools:
            tool = self._tools.get(name)
            if tool is None:
                continue

            async def _call(_name: str = name, **kwargs: Any) -> dict[str, Any]:
                return await self.invoke(ctx, _name, kwargs)

            tools.append(
                StructuredTool.from_function(
                    coroutine=_call,
                    name=tool.name,
                    description=tool.description,
                    args_schema=tool.args_schema,
                )
            )
        return tools
