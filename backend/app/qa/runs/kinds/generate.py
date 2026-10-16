"""`generate` run: grounded test cases for every requirement that has a matching screen.

Rules produce the mechanical cases (field validation, boundaries, role access, positive form flows);
the author agent writes business-rule and end-to-end scenarios; the validator drops anything the
cards do not support. Only requirements that are mapped to a screen, not rejected, and not awaiting a
wording decision are generated for; the rest are reported with the reason, never silently skipped."""

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete
from sqlmodel import Session, col, select

from app.core.db import engine
from app.qa.agents.spec import AgentSpec
from app.qa.brd.embed import embed_texts
from app.qa.config import GenerationConfig
from app.qa.generation.author import PROMPT_VERSION, author, ground
from app.qa.generation.gather import (
    RoleAccess,
    forms_for_states,
    role_access,
)
from app.qa.generation.rules import clip, form_cases, role_cases
from app.qa.generation.schema import CaseDraft
from app.qa.generation.validate import dedup
from app.qa.generation.versions import version_content
from app.qa.llm.gateway import LLMConfigError, UsageMeter, get_chat_model
from app.qa.mapping.cards import ScreenCard, load_cards
from app.qa.mapping.gaps import DEFAULT_THRESHOLD, effective
from app.qa.mapping.paths import build_planner
from app.qa.models import (
    Requirement,
    RequirementMapping,
    Run,
    TestCase,
    TestCaseVersion,
    UiState,
)
from app.qa.reference.load import load_refs, load_refs_sync
from app.qa.runs.context import RunContext

logger = logging.getLogger(__name__)
CONCURRENCY = 4
MAX_AUTHOR_CARDS = 4
BEHAVIOURAL = {"functional", "business_rule", "acceptance_criterion", "validation"}
AUTHOR_TYPES = {"positive", "negative", "e2e", "business_rule"}
KEY_RE = re.compile(r"TC-(\d+)$")


@dataclass
class ReqPlan:
    req: Requirement
    cards: list[ScreenCard]
    rule_drafts: list[CaseDraft] = field(default_factory=list)
    rule_clipped: int = 0
    access_only: bool = (
        False  # an access-rights requirement: no field tests of the screens it maps to
    )


@dataclass
class Plan:
    snapshot_id: uuid.UUID
    plans: list[ReqPlan]
    skipped: list[dict[str, str]]
    unreachable: int
    start_title: str
    roles: dict[uuid.UUID, str]
    existing_signatures: set[str]
    next_number: int


def latest_mapped_snapshot(project_id: uuid.UUID) -> uuid.UUID | None:
    with Session(engine) as s:
        return s.exec(
            select(RequirementMapping.snapshot_id)
            .where(RequirementMapping.project_id == project_id)
            .order_by(col(RequirementMapping.updated_at).desc())
        ).first()


FORM_CONFIDENCE = 0.75  # mapping confidence needed to write field tests for a screen
ACCESS_RE = re.compile(r"\b(access|permissions?|privileges?|roles?|rights)\b", re.I)


def _prepare(
    project_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    cfg: GenerationConfig,
    threshold: float,
    only: set[uuid.UUID] | None,
) -> Plan:
    with Session(engine) as s:
        refs = load_refs(s, project_id)
        reqs = list(
            s.exec(
                select(Requirement)
                .where(
                    Requirement.project_id == project_id,
                    Requirement.review_status != "rejected",
                )
                .order_by(col(Requirement.created_at), col(Requirement.req_key))
            ).all()
        )
        if only:
            reqs = [r for r in reqs if r.id in only]
        for r in reqs:
            s.expunge(r)
        maps = s.exec(
            select(RequirementMapping).where(
                RequirementMapping.snapshot_id == snapshot_id
            )
        ).all()
        cards = load_cards(s, snapshot_id, hide_row_data=True)
        planner = build_planner(s, snapshot_id)
        unreachable = 0
        by_state = {sid: c for c in cards for sid in c.state_ids}
        access: RoleAccess = role_access(s, snapshot_id, project_id)
        roles = dict(access.roles)
        existing = s.exec(
            select(TestCase.signature, TestCase.status).where(
                TestCase.project_id == project_id
            )
        ).all()
        keys = s.exec(
            select(TestCase.tc_key).where(TestCase.project_id == project_id)
        ).all()
        nums = [int(m.group(1)) for k in keys if (m := KEY_RE.match(k))]
        plans: list[ReqPlan] = []
        skipped: list[dict[str, str]] = []
        for r in reqs:
            why: str | None = None
            if (
                r.ambiguity_flag
                and not r.ambiguity_resolved
                and not cfg.include_flagged_requirements
            ):
                why = "its wording is flagged as ambiguous and needs a decision"
            elif r.type == "nfr":
                why = (
                    "non-functional requirements cannot be tested through the interface"
                )
            mine = sorted(
                (
                    m
                    for m in maps
                    if m.requirement_id == r.id and effective(m, threshold)
                ),
                key=lambda mp: -mp.confidence,
            )
            if why is None and not mine:
                why = (
                    "no screen is mapped to it (run mapping, or link a screen by hand)"
                )
            if why is not None:
                skipped.append({"req_key": r.req_key, "reason": why})
                continue
            mcards: list[ScreenCard] = []
            for mp in mine:
                c = by_state.get(mp.state_id)
                if c is not None and c not in mcards:
                    mcards.append(c)
            if not mcards:
                skipped.append(
                    {
                        "req_key": r.req_key,
                        "reason": "its mapped screens are no longer in the snapshot",
                    }
                )
                continue
            plan = ReqPlan(r, mcards)
            # Field tests are written only for screens the requirement is *confidently* about: a weak link
            # (50-75%, not confirmed by a person) would hang another feature's field rules on it.
            solid = {
                mp.state_id
                for mp in mine
                if mp.confidence >= FORM_CONFIDENCE or mp.review_status == "confirmed"
            }
            rep_ids = [c.state_id for c in mcards if c.state_id in solid]
            destructive = {d for c in mcards for d in c.destructive}
            drafts: list[CaseDraft] = []
            # An access-rights requirement ("Clerk limited access") maps to every screen the role sees; field
            # tests of those screens belong to the requirements about the forms, not to this one.
            access_req = bool(ACCESS_RE.search(r.title)) and (
                r.key_origin != "brd" or len(mcards) > 3
            )
            found_forms, lost = (
                ([], 0)
                if access_req
                else forms_for_states(s, snapshot_id, rep_ids, planner)
            )
            plan.access_only = access_req
            unreachable += lost
            for fctx in (
                found_forms
            ):  # rules the API specification adds to what the screen shows
                fctx.fields, fctx.api_source = refs.enrich(
                    fctx.fields,
                    f"{r.title} {r.description} {fctx.parent_title} {fctx.state.title or ''}",
                )
            for ctx in found_forms:
                rids = access.visible.get(ctx.state.id) or access.visible.get(
                    ctx.parent_state_id, set()
                )
                names = sorted(access.roles[x] for x in rids if x in access.roles)
                role = names[0] if names else None
                per_form, cut = clip(
                    form_cases(ctx, r.req_key, role, destructive),
                    cfg.max_rule_cases_per_form,
                )
                if ctx.state.module == "Sign in":
                    # the sign-in screen is seen by a visitor who is not signed in
                    for d in per_form:
                        d.role_name = None
                        d.preconditions = [
                            "The tester is signed out (a visitor, not signed in)",
                            *[p for p in d.preconditions if "signed in as" not in p],
                        ]
                drafts += per_form
                plan.rule_clipped += cut
            if "role_access" in cfg.test_types:
                seen_titles: set[str] = set()
                for c in mcards:
                    st = s.get(UiState, c.state_id)
                    if st is None or st.kind != "page" or st.module == "Sign in":
                        continue
                    # one access case per screen *name*: a list opened with different filters or under
                    # different projects is the same screen to a tester
                    title_key = (st.heading or st.title or "").strip().lower()
                    if title_key in seen_titles:
                        continue
                    seen_titles.add(title_key)
                    if c.path is None:
                        unreachable += 1
                        continue
                    role_paths = {
                        name: found[0]
                        for rid, name in access.roles.items()
                        if (found := planner.plan(c.state_id, rid)) is not None
                    }
                    drafts += role_cases(
                        st,
                        r.req_key,
                        access,
                        c.path,
                        planner.start_title,
                        c.state_ids,
                        role_paths,
                    )
            plan.rule_drafts = [d for d in drafts if d.test_type in cfg.test_types]
            plans.append(plan)
    return Plan(
        snapshot_id=snapshot_id,
        plans=plans,
        skipped=skipped,
        unreachable=unreachable,
        start_title=planner.start_title,
        roles=roles,
        existing_signatures={sig for sig, status in existing if status != "draft"},
        next_number=(max(nums) if nums else 0) + 1,
    )


def _case_text(d: CaseDraft) -> str:
    return f"{d.title}. {d.expected_result}. " + " ".join(d.rendered())


def _persist(
    feedback: str,
    run_id: uuid.UUID,
    project_id: uuid.UUID,
    plan: Plan,
    kept: list[CaseDraft],
    vectors: list[Any],
    req_ids: dict[str, uuid.UUID],
    regenerated: set[uuid.UUID] | None,
) -> int:
    role_ids = {name: rid for rid, name in plan.roles.items()}
    with Session(engine) as s:
        # Re-generating replaces unreviewed drafts; approved / edited / rejected cases are kept.
        # A full run also clears drafts of requirements that can no longer be generated for (for
        # example a mapping that was rejected since): leaving them would keep cases nothing supports.
        stale = delete(TestCase).where(
            col(TestCase.project_id) == project_id,
            col(TestCase.status).in_(["draft", "needs_regen"]),
        )
        if regenerated is not None:
            stale = stale.where(col(TestCase.requirement_id).in_(list(regenerated)))
        s.execute(stale)
        n = plan.next_number
        for d, vec in zip(kept, vectors, strict=True):
            case = TestCase(
                project_id=project_id,
                tc_key=f"TC-{n:04d}",
                requirement_id=req_ids[d.requirement_key],
                snapshot_id=plan.snapshot_id,
                state_id=d.state_id,
                role_id=role_ids.get(d.role_name or ""),
                role_name=d.role_name,
                module=d.module,
                title=d.title,
                preconditions=d.preconditions,
                steps=[st.model_dump(mode="json") for st in d.steps],
                rendered_steps=d.rendered(),
                test_data=d.test_data(),
                expected_result=d.expected_result,
                priority=d.priority,
                test_type=d.test_type,
                origin=d.origin,
                evidence=d.evidence,
                signature=d.evidence.get("signature", d.signature()),
                embedding=vec,
                run_id=run_id,
            )
            s.add(case)
            s.add(
                TestCaseVersion(
                    test_case_id=case.id,
                    version=1,
                    content=version_content(case),
                    change_type="regenerated" if feedback else "generated",
                    change_note=feedback[:500],
                )
            )
            n += 1
        s.commit()
    return len(kept)


def _link_run(run_id: uuid.UUID, snapshot_id: uuid.UUID) -> None:
    with Session(engine) as s:
        run = s.get(Run, run_id)
        if run is not None:
            run.snapshot_id = snapshot_id
            s.add(run)
            s.commit()


async def run_generate(rc: RunContext) -> dict[str, Any]:
    cfg_in = rc.run.config_snapshot
    project_id = rc.project.id
    events, agent_id = rc.events, str(uuid.uuid4())
    cfg = GenerationConfig.model_validate(rc.project.generation_config or {})
    threshold = float(
        rc.project.generation_config.get("mapping_threshold") or DEFAULT_THRESHOLD
    )
    only = (
        {uuid.UUID(str(x)) for x in cfg_in["requirement_ids"]}
        if cfg_in.get("requirement_ids")
        else None
    )
    refs = await asyncio.to_thread(load_refs_sync, project_id)
    spec = AgentSpec(kind="test_author", goal="Generate grounded test cases")
    await events.emit("agent_spawned", agent_id=agent_id, spec=spec.model_dump())
    await events.emit("agent_status", agent_id=agent_id, status="running")

    async def fail(message: str) -> None:
        await events.emit("error", agent_id=agent_id, message=message)
        await events.emit(
            "agent_finished",
            agent_id=agent_id,
            status="failed",
            detail=message[:200],
            budget={},
        )
        raise RuntimeError(message)

    snapshot_id = (
        uuid.UUID(str(cfg_in["snapshot_id"]))
        if cfg_in.get("snapshot_id")
        else await asyncio.to_thread(latest_mapped_snapshot, project_id)
    )
    if snapshot_id is None:
        await fail("Nothing is mapped to screens yet. Run mapping first.")
        raise AssertionError  # unreachable
    await asyncio.to_thread(_link_run, rc.run.id, snapshot_id)
    plan = await asyncio.to_thread(
        _prepare, project_id, snapshot_id, cfg, threshold, only
    )
    if not plan.plans:
        await fail(
            "No requirement can be generated for: "
            + "; ".join(f"{x['req_key']} ({x['reason']})" for x in plan.skipped[:6])
        )
    await events.emit(
        "generation_started",
        agent_id=agent_id,
        requirements=len(plan.plans),
        skipped=len(plan.skipped),
    )

    meter = UsageMeter()
    use_llm = (
        bool(set(cfg.test_types) & AUTHOR_TYPES) and cfg.llm_cases_per_requirement > 0
    )
    model = None
    llm_error: str | None = None
    if use_llm:
        try:
            model = get_chat_model(
                "test_author",
                overrides=cfg.models or None,
                confidential=rc.project.confidential,
                scanner=rc.scanner,
                meter=meter,
            )
        except LLMConfigError as exc:
            llm_error = f"scenario writing is off: {exc}"
            await events.emit("error", agent_id=agent_id, message=llm_error)

    sem = asyncio.Semaphore(CONCURRENCY)
    stats = {"author_dropped": 0, "author_failed": 0, "rule_clipped": 0}
    problem_sample: list[str] = []
    llm_drafts: dict[str, list[CaseDraft]] = {}
    done = 0
    reported = {"in": 0, "out": 0}
    role_names = set(plan.roles.values())

    async def work(p: ReqPlan) -> None:
        nonlocal done
        r = p.req
        async with sem:
            await rc.control.checkpoint(agent_id)
            drafts: list[CaseDraft] = []
            if model is not None and r.type in BEHAVIOURAL and r.type != "validation":
                cards = p.cards[:MAX_AUTHOR_CARDS]
                feedback = ""
                for _attempt in range(2):
                    try:
                        res = await author(
                            model,
                            r.req_key,
                            r.type,
                            r.title,
                            r.description,
                            r.acceptance_criteria,
                            sorted(role_names),
                            cards,
                            cfg.llm_cases_per_requirement,
                            feedback,
                            str(cfg_in.get("feedback") or ""),
                            refs.glossary_block(f"{r.title} {r.description}")
                            + refs.api_block(
                                f"{r.title} {r.description} "
                                + " ".join(sorted({x for c in cards for x in c.labels}))
                            ),
                        )
                    except Exception as exc:
                        stats["author_failed"] += 1
                        await events.emit(
                            "error",
                            agent_id=agent_id,
                            message=f"{r.req_key}: {type(exc).__name__}: {str(exc)[:200]}",
                        )
                        break
                    good, problems = ground(
                        res,
                        r.req_key,
                        cards,
                        role_names,
                        cfg.llm_cases_per_requirement,
                        plan.start_title,
                    )
                    stats["author_dropped"] += len(problems)
                    problem_sample.extend(f"{r.req_key} {x}" for x in problems[:2])
                    drafts = good
                    if good or not problems:
                        break
                    feedback = "\n".join(problems[:6])
                drafts = [d for d in drafts if d.test_type in cfg.test_types]
            llm_drafts[r.req_key] = drafts
            done += 1
            stats["rule_clipped"] += p.rule_clipped
            await events.emit(
                "cases_generated",
                agent_id=agent_id,
                req_key=r.req_key,
                title=r.title,
                rule=len(p.rule_drafts),
                llm=len(drafts),
            )
            await events.emit(
                "metric",
                agent_id=agent_id,
                kind="generation_progress",
                done=done,
                total=len(plan.plans),
            )
            d_in = meter.totals.input_tokens - reported["in"]
            d_out = meter.totals.output_tokens - reported["out"]
            reported["in"], reported["out"] = (
                meter.totals.input_tokens,
                meter.totals.output_tokens,
            )
            await events.emit(
                "llm_usage", agent_id=agent_id, input_tokens=d_in, output_tokens=d_out
            )

    await asyncio.gather(*(work(p) for p in plan.plans))

    ordered: list[CaseDraft] = []
    for p in plan.plans:  # keep requirement order
        ordered += p.rule_drafts + llm_drafts.get(p.req.req_key, [])
    vectors: list[Any] = [None] * len(ordered)
    embed_error: str | None = None
    try:
        got = await embed_texts(
            [_case_text(d) for d in ordered], confidential=rc.project.confidential
        )
        vectors = list(got)
    except Exception as exc:  # exact de-duplication still runs
        embed_error = f"{type(exc).__name__}: {str(exc)[:160]}"
    result = dedup(
        ordered,
        vectors,
        existing_signatures=plan.existing_signatures,
        threshold=cfg.dedup_cosine,
    )
    req_ids = {p.req.req_key: p.req.id for p in plan.plans}
    saved = await asyncio.to_thread(
        _persist,
        str(cfg_in.get("feedback") or ""),
        rc.run.id,
        project_id,
        plan,
        result.kept,
        result.vectors,
        req_ids,
        {p.req.id for p in plan.plans} if only else None,
    )
    by_type: dict[str, int] = {}
    for d in result.kept:
        by_type[d.test_type] = by_type.get(d.test_type, 0) + 1
    summary = {
        "snapshot_id": str(snapshot_id),
        "prompt_version": PROMPT_VERSION,
        "requirements_generated": len(plan.plans),
        "requirements_skipped": plan.skipped,
        "cases": saved,
        "by_type": by_type,
        "by_origin": {
            "rule": sum(1 for d in result.kept if d.origin == "rule"),
            "llm": sum(1 for d in result.kept if d.origin == "llm"),
        },
        "dropped": {
            "trivial": result.trivial,
            "exact_duplicates": result.exact,
            "near_duplicates": result.semantic,
            "ungrounded_author_cases": stats["author_dropped"],
            "rule_cases_over_limit": stats["rule_clipped"],
            "unreachable_screens_or_forms": plan.unreachable,
        },
        "author_failed": stats["author_failed"],
        "author_problem_sample": problem_sample[:10],
        "notes": [
            n
            for n in (
                llm_error,
                embed_error and f"embeddings unavailable: {embed_error}",
            )
            if n
        ],
        "input_tokens": meter.totals.input_tokens,
        "output_tokens": meter.totals.output_tokens,
    }
    await events.emit(
        "agent_finished",
        agent_id=agent_id,
        status="succeeded",
        result=f"{saved} test cases for {len(plan.plans)} requirements",
        budget={},
    )
    return summary
