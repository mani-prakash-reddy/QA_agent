"""`echo` run: a scripted, LLM-free multi-agent run used to exercise the whole runtime
(supervisor -> parallel sub-agents -> gate -> events -> persistence -> control). Not a product feature."""

import asyncio
import uuid
from typing import Any

from langchain_core.messages import AIMessage
from pydantic import BaseModel

from app.qa.agents.budget import BudgetTracker
from app.qa.agents.runtime.runner import AgentResult, run_agent
from app.qa.agents.spec import AgentSpec, Budget, Scope
from app.qa.gate.gate import AgentContext, ToolDef, ToolGate
from app.qa.llm.fake import ScriptedChatModel
from app.qa.runs.context import RunContext
from app.qa.runs.status import status_from_agents


class _Browse(BaseModel):
    target: str


async def _fake_browse(ctx: AgentContext, args: _Browse) -> dict[str, Any]:
    await asyncio.sleep(ctx.resources.get("delay", 0.3))
    return {"visited": args.target, "elements": 3}


ECHO_GATE = ToolGate(
    [ToolDef("browser_snapshot", "visit a fake page", _Browse, _fake_browse)]
)


def _script(agent_no: int, steps: int) -> list[AIMessage]:
    calls = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "browser_snapshot",
                    "args": {"target": f"/section-{agent_no}/page-{i}"},
                    "id": f"c{agent_no}-{i}",
                }
            ],
        )
        for i in range(steps)
    ]
    return [*calls, AIMessage(content=f"explorer {agent_no} visited {steps} pages")]


async def run_echo(rc: RunContext) -> dict[str, Any]:
    params = rc.run.config_snapshot
    n_agents, steps, delay = (
        int(params.get("agents", 2)),
        int(params.get("steps", 5)),
        float(params.get("delay", 0.3)),
    )
    supervisor_id = str(uuid.uuid4())
    sup_spec = AgentSpec(kind="supervisor", goal="coordinate the echo run", tools=[])
    await rc.events.emit(
        "agent_spawned", agent_id=supervisor_id, spec=sup_spec.model_dump()
    )
    await rc.events.emit("agent_status", agent_id=supervisor_id, status="running")

    async def sub(i: int) -> AgentResult:
        spec = AgentSpec(
            kind="explorer",
            goal=f"explore fake section {i}",
            scope=Scope(role_ref="demo"),
            tools=["browser_snapshot"],
            budget=Budget(max_steps=steps + 2),
        )
        ctx = AgentContext(
            run_id=rc.run_id,
            agent_id=str(uuid.uuid4()),
            parent_id=supervisor_id,
            spec=spec,
            budget=BudgetTracker(spec.budget),
            events=rc.events,
            control=rc.control,
            scanner=rc.scanner,
            resources={"delay": delay},
        )
        model = ScriptedChatModel(script=_script(i, steps))
        return await run_agent(
            ctx, model, ECHO_GATE, "explore", checkpointer=rc.checkpointer
        )

    results = await asyncio.gather(*[sub(i) for i in range(n_agents)])
    statuses = [r.status for r in results]
    status = status_from_agents(statuses)
    await rc.events.emit(
        "agent_finished",
        agent_id=supervisor_id,
        status=status,
        result=f"{len(results)} agents: {statuses}",
        budget={},
    )
    return {"agents": len(results), "statuses": statuses}
