"""`map` run: link each requirement to the screens that implement it, then derive the two-way gaps."""

import asyncio
import logging
import uuid
from typing import Any

from sqlalchemy import delete
from sqlmodel import Session, col, select

from app.core.config import settings
from app.core.db import engine
from app.qa.agents.spec import AgentSpec
from app.qa.brd.embed import embed_texts, requirement_text
from app.qa.llm.gateway import LLMConfigError, UsageMeter, get_chat_model
from app.qa.mapping.cards import ScreenCard, load_cards
from app.qa.mapping.gaps import (
    DEFAULT_THRESHOLD,
    GapHint,
    mapping_pairs,
    recompute_gaps,
)
from app.qa.mapping.judge import PROMPT_VERSION, Judged, judge, verify
from app.qa.mapping.retrieve import Candidate, Retriever
from app.qa.models import AppSnapshot, Requirement, RequirementMapping, Run, UiState
from app.qa.reference.glossary import expansion as glossary_expansion
from app.qa.reference.load import load_refs_sync
from app.qa.runs.context import RunContext

logger = logging.getLogger(__name__)
CONCURRENCY = 4
ATTEMPTS = 2
TOP_K = 6
# The model's 'not visible in the UI' call is trusted for these types only; a functional or
# validation requirement is always judged against the screens (it is a gap, not 'invisible').
BEHAVIOURAL = {"functional", "validation", "acceptance_criterion"}


def latest_snapshot_id(project_id: uuid.UUID) -> uuid.UUID | None:
    with Session(engine) as s:
        snap = s.exec(
            select(AppSnapshot)
            .where(AppSnapshot.project_id == project_id, AppSnapshot.status == "ready")
            .order_by(col(AppSnapshot.created_at).desc())
        ).first()
        return snap.id if snap else None


def _load(
    project_id: uuid.UUID, snapshot_id: uuid.UUID
) -> tuple[list[Requirement], list[ScreenCard]]:
    with Session(engine) as s:
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
        for r in reqs:
            s.expunge(r)
        return reqs, load_cards(s, snapshot_id)


def _state_vectors(cards: list[ScreenCard]) -> dict[str, list[float]]:
    with Session(engine) as s:
        out: dict[str, list[float]] = {}
        for c in cards:
            st = s.get(UiState, c.state_id)
            if (
                st is not None
                and st.embedding is not None
                and st.embedding_model == settings.embedding_model_id
            ):
                out[c.group_key] = [float(x) for x in st.embedding]
        return out


def _save_vectors(
    pairs: list[tuple[uuid.UUID, list[float]]], table: str, model: str
) -> None:
    with Session(engine) as s:
        for ident, vec in pairs:
            row = s.get(UiState if table == "state" else Requirement, ident)
            if row is not None:
                row.embedding, row.embedding_model = vec, model
                s.add(row)
        s.commit()


def _link_run(run_id: uuid.UUID, snapshot_id: uuid.UUID) -> None:
    with Session(engine) as s:
        run = s.get(Run, run_id)
        if run is not None:
            run.snapshot_id = snapshot_id
            s.add(run)
            s.commit()


def _store(
    run_id: uuid.UUID,
    project_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    requirement_id: uuid.UUID,
    cards: dict[str, ScreenCard],
    verdicts: list[Judged],
) -> None:
    with Session(engine) as s:
        s.execute(
            delete(RequirementMapping).where(
                col(RequirementMapping.requirement_id) == requirement_id,
                col(RequirementMapping.snapshot_id) == snapshot_id,
                col(RequirementMapping.review_status) == "proposed",
                col(RequirementMapping.method) == "judge",
            )
        )
        s.flush()
        kept = {
            m.state_id
            for m in s.exec(
                select(RequirementMapping).where(
                    RequirementMapping.requirement_id == requirement_id,
                    RequirementMapping.snapshot_id == snapshot_id,
                )
            ).all()
        }
        for v in verdicts:
            card = cards[v.group_key]
            if any(sid in kept for sid in card.state_ids):
                continue  # a human already decided on this screen
            s.add(
                RequirementMapping(
                    project_id=project_id,
                    requirement_id=requirement_id,
                    snapshot_id=snapshot_id,
                    state_id=card.state_id,
                    confidence=v.confidence,
                    rationale=v.rationale,
                    citations=v.citations,
                    retrieval_score=v.retrieval_score,
                    run_id=run_id,
                )
            )
        s.commit()


def _recompute(
    project_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    run_id: uuid.UUID,
    threshold: float,
    hints: dict[uuid.UUID, GapHint],
) -> dict[str, int]:
    with Session(engine) as s:
        return recompute_gaps(
            s,
            project_id,
            snapshot_id,
            threshold=threshold,
            run_id=run_id,
            hints=hints,
        )


def _rejected(snapshot_id: uuid.UUID) -> set[tuple[uuid.UUID, uuid.UUID]]:
    with Session(engine) as s:
        return mapping_pairs(s, snapshot_id)


async def run_map(rc: RunContext) -> dict[str, Any]:
    cfg = rc.run.config_snapshot
    project_id = rc.project.id
    events, agent_id = rc.events, str(uuid.uuid4())
    threshold = float(
        cfg.get("threshold")
        or rc.project.generation_config.get("mapping_threshold")
        or DEFAULT_THRESHOLD
    )
    snapshot_id = (
        uuid.UUID(str(cfg["snapshot_id"]))
        if cfg.get("snapshot_id")
        else await asyncio.to_thread(latest_snapshot_id, project_id)
    )
    spec = AgentSpec(kind="mapper", goal="Map requirements to application screens")
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

    if snapshot_id is None:
        await fail("There is no finished crawl to map against. Run a crawl first.")
        raise AssertionError  # unreachable
    await asyncio.to_thread(_link_run, rc.run.id, snapshot_id)
    reqs, cards = await asyncio.to_thread(_load, project_id, snapshot_id)
    if not reqs:
        await fail("There are no requirements to map. Upload a BRD first.")
    if not cards:
        await fail("The selected crawl recorded no screens.")
    by_key = {c.group_key: c for c in cards}
    await events.emit(
        "mapping_started",
        agent_id=agent_id,
        snapshot_id=str(snapshot_id),
        requirements=len(reqs),
        screens=len(cards),
        threshold=threshold,
    )

    # ---- embeddings (best effort; retrieval falls back to lexical only)
    vectors = await asyncio.to_thread(_state_vectors, cards)
    embed_errors: list[str] = []
    try:
        missing = [c for c in cards if c.group_key not in vectors]
        if missing:
            new = await embed_texts(
                [c.text for c in missing], confidential=rc.project.confidential
            )
            await asyncio.to_thread(
                _save_vectors,
                [(c.state_id, v) for c, v in zip(missing, new, strict=True)],
                "state",
                settings.embedding_model_id,
            )
            vectors.update({c.group_key: v for c, v in zip(missing, new, strict=True)})
        todo = [
            r
            for r in reqs
            if r.embedding is None or r.embedding_model != settings.embedding_model_id
        ]
        if todo:
            new = await embed_texts(
                [requirement_text(r.title, r.description) for r in todo],
                confidential=rc.project.confidential,
            )
            await asyncio.to_thread(
                _save_vectors,
                [(r.id, v) for r, v in zip(todo, new, strict=True)],
                "requirement",
                settings.embedding_model_id,
            )
            for r, v in zip(todo, new, strict=True):
                r.embedding, r.embedding_model = v, settings.embedding_model_id
    except Exception as exc:  # lexical retrieval still works
        embed_errors.append(f"{type(exc).__name__}: {str(exc)[:160]}")
        await events.emit(
            "error",
            agent_id=agent_id,
            message=f"embeddings unavailable, using keyword retrieval only: {embed_errors[0]}",
        )
    retriever = Retriever(cards, vectors)

    meter = UsageMeter()
    try:
        model = get_chat_model(
            "mapper",
            confidential=rc.project.confidential,
            scanner=rc.scanner,
            meter=meter,
        )
    except LLMConfigError as exc:
        await fail(f"Mapping is not possible: {exc}")
        raise
    rejected = await asyncio.to_thread(_rejected, snapshot_id)
    sem = asyncio.Semaphore(CONCURRENCY)
    hints: dict[uuid.UUID, GapHint] = {}
    stats = {"matched": 0, "weak": 0, "unmatched": 0, "not_ui": 0, "failed": 0}
    done = 0
    reported = {"in": 0, "out": 0}
    refs = await asyncio.to_thread(load_refs_sync, rc.project.id)

    async def work(r: Requirement) -> None:
        nonlocal done
        async with sem:
            await rc.control.checkpoint(agent_id)
            query = " ".join(
                [r.title, r.description, *[str(a) for a in r.acceptance_criteria]]
            )
            gloss = refs.glossary_block(query)
            if refs.terms:  # the business's own words for the same thing help retrieval
                query += " " + glossary_expansion(refs.terms, query)
            qvec = (
                [float(x) for x in r.embedding]
                if r.embedding is not None and vectors
                else None
            )
            found = retriever.search(query, qvec, TOP_K)
            found = [
                c
                for c in found
                if not any((r.id, sid) in rejected for sid in c.card.state_ids)
            ]
            verdicts: list[Judged] = []
            outcome = "unmatched"
            if not found and r.type == "nfr":
                hints[r.id] = GapHint(
                    "not_ui_observable",
                    "This requirement cannot be verified from the user interface "
                    "(e.g. performance, security or backend behaviour).",
                )
                stats["not_ui"] += 1
                outcome = "not_ui"
            elif not found:
                hints[r.id] = GapHint(
                    "no_candidate",
                    "No crawled screen shares any vocabulary with this requirement.",
                )
                stats["unmatched"] += 1
            else:
                result = None
                for attempt in range(ATTEMPTS):
                    try:
                        result = await judge(
                            model,
                            r.req_key,
                            r.type,
                            r.title,
                            r.description,
                            r.acceptance_criteria,
                            found,
                            context=gloss,
                        )
                        break
                    except Exception as exc:
                        if attempt == ATTEMPTS - 1:
                            await events.emit(
                                "error",
                                agent_id=agent_id,
                                message=f"{r.req_key}: {type(exc).__name__}: {str(exc)[:200]}",
                            )
                if result is None:
                    stats["failed"] += 1
                    hints[r.id] = GapHint(
                        "not_assessed",
                        "The judge could not assess this requirement (model error); run mapping again.",
                    )
                    outcome = "failed"
                elif not result.ui_observable and r.type not in BEHAVIOURAL:
                    hints[r.id] = GapHint(
                        "not_ui_observable",
                        "This requirement cannot be verified from the user interface "
                        "(e.g. performance, security or backend behaviour).",
                    )
                    stats["not_ui"] += 1
                    outcome = "not_ui"
                else:
                    verdicts = verify(result, found)
                    if not verdicts and r.type in BEHAVIOURAL:
                        # An empty answer makes a gap (a false gap costs a reviewer's time and
                        # hides a feature), so a behavioural requirement gets one second look.
                        try:
                            again = await judge(
                                model,
                                r.req_key,
                                r.type,
                                r.title,
                                r.description,
                                r.acceptance_criteria,
                                found,
                                second_look=True,
                                context=gloss,
                            )
                            verdicts = verify(again, found)
                        except Exception:  # the first answer stands
                            logger.warning("second look failed for %s", r.req_key)
                    best = verdicts[0].confidence if verdicts else 0.0
                    if best >= threshold:
                        stats["matched"] += 1
                        outcome = "matched"
                    else:
                        stats["weak" if verdicts else "unmatched"] += 1
                        outcome = "weak" if verdicts else "unmatched"
                        hints[r.id] = GapHint(
                            "judged_absent",
                            "The candidate screens do not show this behaviour."
                            if not verdicts
                            else "Only weak evidence was found on the nearest screens.",
                            {
                                "nearest": [
                                    {
                                        "screen": c.card.display,
                                        "route": c.card.route,
                                        "retrieval": c.score,
                                    }
                                    for c in found[:3]
                                ]
                            },
                        )
            if outcome != "failed":
                await asyncio.to_thread(
                    _store, rc.run.id, project_id, snapshot_id, r.id, by_key, verdicts
                )
            done += 1
            top = verdicts[0] if verdicts else None
            await events.emit(
                "requirement_mapped",
                agent_id=agent_id,
                req_key=r.req_key,
                title=r.title,
                outcome=outcome,
                confidence=top.confidence if top else None,
                screen=by_key[top.group_key].display if top else None,
            )
            await events.emit(
                "metric",
                agent_id=agent_id,
                kind="mapping_progress",
                done=done,
                total=len(reqs),
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

    await asyncio.gather(*(work(r) for r in reqs))
    if stats["failed"] > len(reqs) / 2:
        await fail(
            f"the judge failed for {stats['failed']} of {len(reqs)} requirements"
        )

    gap_stats = await asyncio.to_thread(
        _recompute, project_id, snapshot_id, rc.run.id, threshold, hints
    )
    await events.emit("gaps_computed", agent_id=agent_id, stats=gap_stats)
    summary = {
        "snapshot_id": str(snapshot_id),
        "threshold": threshold,
        "prompt_version": PROMPT_VERSION,
        "embedding_errors": embed_errors,
        "input_tokens": meter.totals.input_tokens,
        "output_tokens": meter.totals.output_tokens,
        **stats,
        **gap_stats,
    }
    await events.emit(
        "agent_finished",
        agent_id=agent_id,
        status="succeeded",
        result=(
            f"{gap_stats['requirements_matched']}/{gap_stats['requirements_total']} requirements matched; "
            f"{gap_stats['feature_gaps']} undocumented screens"
        ),
        budget={},
    )
    return summary


__all__ = ["Candidate", "run_map"]
