import asyncio
import json
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from pydantic import BaseModel
from redis.asyncio import Redis

from app.qa.agents.budget import BudgetExceeded
from app.qa.agents.runtime.runner import run_agent
from app.qa.agents.spec import Budget
from app.qa.events import Event, EventPublisher, stream_key
from app.qa.gate.gate import AgentContext, ToolBlocked, ToolDef, ToolGate
from app.qa.llm.fake import ScriptedChatModel
from app.qa.runs.control import RunControl, StopRequested


class Echo(BaseModel):
    text: str


async def echo(_ctx: AgentContext, a: Echo) -> dict[str, Any]:
    return {"echo": a.text}


async def leaky(_ctx: AgentContext, _a: Echo) -> dict[str, Any]:
    return {"page_text": "the password is hunter2-secret ok"}


async def destructive(_ctx: AgentContext, _a: Echo) -> dict[str, Any]:
    raise ToolBlocked("denylist:delete")


async def boom(_ctx: AgentContext, _a: Echo) -> dict[str, Any]:
    raise RuntimeError("browser crashed")


GATE = ToolGate(
    [
        ToolDef("echo", "echo text", Echo, echo),
        ToolDef("leaky", "returns a secret", Echo, leaky),
        ToolDef("destructive", "blocked action", Echo, destructive),
        ToolDef("boom", "fails", Echo, boom),
    ]
)


async def read_events(redis: Redis, run_id: str) -> list[Event]:
    rows = await redis.xrange(stream_key(run_id))
    return [Event.model_validate_json(f["data"]) for _, f in rows]


@pytest.mark.anyio
async def test_gate_happy_path_emits_events(
    make_ctx, redis: Redis, run_id: str
) -> None:  # type: ignore[no-untyped-def]
    ctx = make_ctx(["echo"])
    assert await GATE.invoke(ctx, "echo", {"text": "hi"}) == {"echo": "hi"}
    types = [e.type for e in await read_events(redis, run_id)]
    assert types == ["tool_call", "tool_result"]
    assert ctx.budget.steps == 1


@pytest.mark.anyio
async def test_gate_rejects_unauthorized_and_invalid_calls(
    make_ctx, redis: Redis, run_id: str
) -> None:  # type: ignore[no-untyped-def]
    ctx = make_ctx(["echo"])
    assert "not available" in (await GATE.invoke(ctx, "leaky", {"text": "x"}))["error"]
    assert "invalid arguments" in (await GATE.invoke(ctx, "echo", {}))["error"]
    assert ctx.budget.steps == 2  # rejected calls still burn budget
    assert any(e.type == "blocked" for e in await read_events(redis, run_id))


@pytest.mark.anyio
async def test_gate_redacts_secrets_in_results_and_events(
    make_ctx, redis: Redis, run_id: str
) -> None:  # type: ignore[no-untyped-def]
    ctx = make_ctx(["leaky"], secrets=("hunter2-secret",))
    result = await GATE.invoke(ctx, "leaky", {"text": "x"})
    assert "hunter2-secret" not in json.dumps(result)
    raw = json.dumps(
        [e.model_dump(mode="json") for e in await read_events(redis, run_id)]
    )
    assert "hunter2-secret" not in raw and "[REDACTED]" in raw


@pytest.mark.anyio
async def test_gate_blocked_and_error_become_results_not_crashes(
    make_ctx, redis: Redis, run_id: str
) -> None:  # type: ignore[no-untyped-def]
    ctx = make_ctx(["destructive", "boom"])
    assert await GATE.invoke(ctx, "destructive", {"text": "x"}) == {
        "blocked": True,
        "reason": "denylist:delete",
    }
    assert "browser crashed" in (await GATE.invoke(ctx, "boom", {"text": "x"}))["error"]
    types = [e.type for e in await read_events(redis, run_id)]
    assert "blocked" in types and "error" in types


@pytest.mark.anyio
async def test_gate_enforces_step_budget(make_ctx) -> None:  # type: ignore[no-untyped-def]
    ctx = make_ctx(["echo"], budget=Budget(max_steps=2))
    await GATE.invoke(ctx, "echo", {"text": "1"})
    await GATE.invoke(ctx, "echo", {"text": "2"})
    with pytest.raises(BudgetExceeded):
        await GATE.invoke(ctx, "echo", {"text": "3"})


@pytest.mark.anyio
async def test_event_sequence_is_monotonic_and_sink_gets_persistable_events(
    redis: Redis, run_id: str
) -> None:
    persisted: list[Event] = []

    async def sink(batch: list[Event]) -> None:
        persisted.extend(batch)

    pub = EventPublisher(redis, run_id, sink=sink, flush_interval=0.05)
    await pub.start()
    await asyncio.gather(
        *[pub.emit("metric", agent_id=f"a{i}", n=i) for i in range(20)]
    )
    await pub.emit("llm_token", agent_id="a0", text="hel")  # live only
    await pub.aclose()
    seqs = sorted(e.seq for e in await read_events(redis, run_id))
    assert seqs == list(range(1, 22))
    assert len(persisted) == 20 and all(e.type != "llm_token" for e in persisted)


@pytest.mark.anyio
async def test_sink_batches_never_overlap_and_arrive_in_order(
    redis: Redis, run_id: str
) -> None:
    """Regression: concurrent flushes let agent_finished be written before agent_spawned."""
    active, overlaps, written = 0, 0, []

    async def slow_sink(batch: list[Event]) -> None:
        nonlocal active, overlaps
        active += 1
        overlaps += active > 1
        await asyncio.sleep(0.02)
        written.append([e.payload["n"] for e in batch])
        active -= 1

    pub = EventPublisher(
        redis, run_id, sink=slow_sink, flush_interval=0.005, batch_size=3
    )
    await pub.start()
    for n in range(30):  # sequential emits: buffer order == n
        await pub.emit("metric", n=n)
    await pub.aclose()
    flat = [n for batch in written for n in batch]
    assert overlaps == 0
    assert flat == list(range(30))


@pytest.mark.anyio
async def test_control_pause_resume_stop(redis: Redis, run_id: str) -> None:
    control = RunControl(redis, run_id, poll_interval=0.02)
    await control.checkpoint("a1")  # running by default
    await control.set("paused")
    task = asyncio.create_task(control.checkpoint("a1"))
    await asyncio.sleep(0.1)
    assert not task.done()  # blocked while paused
    await control.set("running")
    await asyncio.wait_for(task, 1)
    await control.set("stopped", agent_id="a1")  # agent-level stop
    with pytest.raises(StopRequested):
        await control.checkpoint("a1")
    await control.checkpoint("a2")  # other agents unaffected
    await control.set("stopped")  # run-level stop wins
    with pytest.raises(StopRequested):
        await control.checkpoint("a2")


def tool_call(name: str, i: int, **args: Any) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args or {"text": "x"}, "id": f"c{i}"}],
    )


@pytest.mark.anyio
async def test_agent_runs_through_gate_and_finishes(
    make_ctx, redis: Redis, run_id: str
) -> None:  # type: ignore[no-untyped-def]
    ctx = make_ctx(["echo", "destructive"])
    model = ScriptedChatModel(
        script=[
            tool_call("echo", 1, text="a"),
            tool_call("destructive", 2),
            AIMessage(content="all done"),
        ]
    )
    result = await run_agent(ctx, model, GATE, "go")
    assert (result.status, result.final) == ("succeeded", "all done")
    types = [e.type for e in await read_events(redis, run_id)]
    for expected in (
        "agent_spawned",
        "tool_call",
        "tool_result",
        "blocked",
        "llm_usage",
        "agent_finished",
    ):
        assert expected in types
    assert ctx.budget.tokens > 0


@pytest.mark.anyio
async def test_agent_stops_cleanly_when_stop_requested(
    make_ctx, redis: Redis, run_id: str
) -> None:  # type: ignore[no-untyped-def]
    ctx = make_ctx(["echo"])
    await RunControl(redis, run_id).set("stopped")
    model = ScriptedChatModel(script=[tool_call("echo", 1), AIMessage(content="never")])
    result = await run_agent(ctx, model, GATE, "go")
    assert result.status == "stopped"


@pytest.mark.anyio
async def test_agent_ends_with_budget_exceeded_when_it_loops(make_ctx) -> None:  # type: ignore[no-untyped-def]
    ctx = make_ctx(["echo"], budget=Budget(max_steps=3))
    model = ScriptedChatModel(
        script=[tool_call("echo", 1)]
    )  # repeats the same call forever
    result = await asyncio.wait_for(run_agent(ctx, model, GATE, "go"), 10)
    assert result.status == "budget_exceeded"
    assert ctx.budget.steps <= 3
