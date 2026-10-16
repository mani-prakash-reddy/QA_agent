"""`ingest` run: BRD file -> chunks -> grounded, flagged, numbered requirements -> embeddings."""

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import delete
from sqlmodel import Session, col, select

from app.core.config import settings
from app.core.db import engine
from app.qa.agents.spec import AgentSpec
from app.qa.brd.chunk import Chunk, chunk_document
from app.qa.brd.embed import embed_texts, requirement_text
from app.qa.brd.extract import (
    Candidate,
    assign_keys,
    extract_chunk,
    finalize_reasons,
    ground_candidates,
    merge_duplicates,
    normalize,
)
from app.qa.brd.parse import BrdParseError, parse_bytes
from app.qa.brd.prompts import PROMPT_VERSION
from app.qa.llm.gateway import LLMConfigError, UsageMeter, get_chat_model
from app.qa.models import BrdChunk, BrdDocument, Requirement
from app.qa.runs.context import RunContext

logger = logging.getLogger(__name__)
CONCURRENCY = 4
ATTEMPTS = 2


def _set_doc(document_id: uuid.UUID, **fields: Any) -> None:
    with Session(engine) as s:
        doc = s.get(BrdDocument, document_id)
        if doc:
            for k, v in fields.items():
                setattr(doc, k, v)
            s.add(doc)
            s.commit()


def _load(document_id: uuid.UUID) -> tuple[BrdDocument, bytes]:
    with Session(engine) as s:
        doc = s.get(BrdDocument, document_id)
        if doc is None:
            raise ValueError("document not found")
        s.expunge(doc)
    return doc, (Path(settings.ARTIFACT_DIR).resolve() / doc.artifact_path).read_bytes()


def _store_chunks(document_id: uuid.UUID, chunks: list[Chunk]) -> list[uuid.UUID]:
    with Session(engine) as s:
        s.execute(delete(BrdChunk).where(col(BrdChunk.document_id) == document_id))
        rows = [
            BrdChunk(
                document_id=document_id,
                ordinal=c.ordinal,
                section_path=c.section_path[:1000],
                page_from=c.page_from,
                page_to=c.page_to,
                text=c.text,
                kind=c.kind,
            )
            for c in chunks
        ]
        s.add_all(rows)
        s.commit()
        return [r.id for r in rows]


def _existing(project_id: uuid.UUID) -> tuple[set[str], set[str]]:
    with Session(engine) as s:
        rows = s.exec(
            select(Requirement.req_key, Requirement.source_anchor).where(
                Requirement.project_id == project_id
            )
        ).all()
    return {k for k, _ in rows}, {
        normalize(str(a.get("quote", ""))) for _, a in rows if a
    }


def _store_requirements(
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    chunk_ids: list[uuid.UUID],
    cands: list[Candidate],
    flags: dict[str, bool],
) -> list[uuid.UUID]:
    with Session(engine) as s:
        rows = []
        for c in cands:
            rows.append(
                Requirement(
                    project_id=project_id,
                    document_id=document_id,
                    req_key=c.key,
                    key_origin=c.key_origin,
                    type=c.data.type,
                    title=c.data.title.strip()[:500],
                    description=c.data.description.strip(),
                    actors=[a for a in c.data.actors if a],
                    module=(c.data.module or None),
                    acceptance_criteria=[a for a in c.data.acceptance_criteria if a],
                    source_anchor={
                        "chunk_id": str(chunk_ids[c.chunk.ordinal]),
                        "section": c.chunk.section_path,
                        "page": c.chunk.page_from,
                        "quote": c.quote,
                    },
                    ambiguity_flag=flags[c.key],
                    ambiguity_reasons=[r.as_dict() for r in c.reasons],
                )
            )
        s.add_all(rows)
        s.commit()
        return [r.id for r in rows]


def _store_embeddings(
    ids: list[uuid.UUID], vectors: list[list[float]], model: str
) -> None:
    with Session(engine) as s:
        for rid, vec in zip(ids, vectors, strict=True):
            r = s.get(Requirement, rid)
            if r:
                r.embedding, r.embedding_model = vec, model
                s.add(r)
        s.commit()


async def run_ingest(rc: RunContext) -> dict[str, Any]:
    document_id = uuid.UUID(str(rc.run.config_snapshot["document_id"]))
    events, agent_id = rc.events, str(uuid.uuid4())
    doc, data = await asyncio.to_thread(_load, document_id)
    spec = AgentSpec(
        kind="extractor", goal=f"Extract requirements from {doc.filename}", tools=[]
    )
    await events.emit("agent_spawned", agent_id=agent_id, spec=spec.model_dump())
    await events.emit("agent_status", agent_id=agent_id, status="running")

    async def fail(message: str) -> None:
        await asyncio.to_thread(
            _set_doc, document_id, status="failed", error=message[:1000]
        )
        await events.emit("error", agent_id=agent_id, message=message)
        await events.emit(
            "agent_finished",
            agent_id=agent_id,
            status="failed",
            detail=message[:200],
            budget={},
        )
        raise RuntimeError(message)

    # ---- parse + chunk
    await asyncio.to_thread(_set_doc, document_id, status="parsing", error=None)
    try:
        parsed = await asyncio.to_thread(parse_bytes, data, doc.filename)
    except BrdParseError as exc:
        await fail(str(exc))
        raise  # unreachable; keeps type checkers happy
    chunks = chunk_document(parsed)
    chunk_ids = await asyncio.to_thread(_store_chunks, document_id, chunks)
    await asyncio.to_thread(
        _set_doc,
        document_id,
        status="extracting",
        page_count=parsed.page_count,
        chunk_count=len(chunks),
    )
    await events.emit(
        "document_parsed",
        agent_id=agent_id,
        filename=doc.filename,
        format=parsed.format,
        pages=parsed.page_count,
        chunks=len(chunks),
        tables=sum(1 for b in parsed.blocks if b.kind == "table"),
        headings=sum(1 for b in parsed.blocks if b.kind == "heading"),
        warnings=parsed.warnings,
    )

    # ---- extract (parallel, retried, grounded)
    meter = UsageMeter()
    try:
        model = get_chat_model(
            "extractor",
            confidential=rc.project.confidential,
            scanner=rc.scanner,
            meter=meter,
        )
    except LLMConfigError as exc:
        # The API holds no LLM keys by design; only the worker can tell the model is missing.
        await fail(f"Extraction is not possible: {exc}")
        raise
    sem = asyncio.Semaphore(CONCURRENCY)
    done = 0
    accepted: dict[int, list[Candidate]] = {}
    stats = {"rejected_quotes": 0, "failed_chunks": 0}
    reported = {"in": 0, "out": 0}

    async def work(chunk: Chunk) -> None:
        nonlocal done
        async with sem:
            await rc.control.checkpoint(agent_id)
            result = None
            for attempt in range(ATTEMPTS):
                try:
                    result = await extract_chunk(model, chunk)
                    break
                except Exception as exc:
                    if attempt == ATTEMPTS - 1:
                        stats["failed_chunks"] += 1
                        await events.emit(
                            "error",
                            agent_id=agent_id,
                            message=f"chunk {chunk.ordinal}: {type(exc).__name__}: {str(exc)[:200]}",
                        )
            if result is not None:
                cands, rejected = ground_candidates(chunk, result)
                stats["rejected_quotes"] += rejected
                accepted[chunk.ordinal] = cands
                for c in cands:
                    await events.emit(
                        "requirement_extracted",
                        agent_id=agent_id,
                        title=c.data.title,
                        req_type=c.data.type,
                        native_id=c.data.native_id,
                        section=chunk.section_path,
                        ambiguous=c.data.ambiguous,
                    )
                if rejected:
                    await events.emit(
                        "skipped",
                        agent_id=agent_id,
                        reason="ungrounded_quote",
                        label=f"{rejected} requirement(s) from '{chunk.section_path[-60:]}'",
                    )
            done += 1
            await events.emit(
                "metric",
                agent_id=agent_id,
                kind="ingest_progress",
                done=done,
                total=len(chunks),
            )
            # usage events are deltas (the UI sums them); the meter holds running totals
            d_in = meter.totals.input_tokens - reported["in"]
            d_out = meter.totals.output_tokens - reported["out"]
            reported["in"], reported["out"] = (
                meter.totals.input_tokens,
                meter.totals.output_tokens,
            )
            await events.emit(
                "llm_usage", agent_id=agent_id, input_tokens=d_in, output_tokens=d_out
            )

    await asyncio.gather(*(work(c) for c in chunks))
    if stats["failed_chunks"] > len(chunks) / 2:
        await fail(
            f"extraction failed for {stats['failed_chunks']} of {len(chunks)} sections"
        )

    # ---- merge, de-duplicate against the project, flag, number
    keys, quotes = await asyncio.to_thread(_existing, rc.project.id)
    ordered = [c for ordinal in sorted(accepted) for c in accepted[ordinal]]
    merged = merge_duplicates(ordered)
    fresh = [c for c in merged if normalize(c.quote) not in quotes]
    skipped_existing = len(merged) - len(fresh)
    assign_keys(fresh, keys)
    flags = {c.key: finalize_reasons(c) for c in fresh}
    req_ids = await asyncio.to_thread(
        _store_requirements, rc.project.id, document_id, chunk_ids, fresh, flags
    )

    # ---- embeddings (best effort)
    embedded = 0
    if fresh:
        try:
            vectors = await embed_texts(
                [requirement_text(c.data.title, c.data.description) for c in fresh],
                confidential=rc.project.confidential,
            )
            await asyncio.to_thread(
                _store_embeddings, req_ids, vectors, settings.embedding_model_id
            )
            embedded = len(vectors)
        except Exception as exc:
            await events.emit(
                "error",
                agent_id=agent_id,
                message=f"embeddings unavailable: {type(exc).__name__}: {str(exc)[:160]}",
            )

    total = len(fresh) + skipped_existing
    await asyncio.to_thread(
        _set_doc, document_id, status="ready", requirement_count=total, run_id=rc.run.id
    )
    summary = {
        "document_id": str(document_id),
        "chunks": len(chunks),
        "requirements": len(fresh),
        "flagged": sum(flags.values()),
        "skipped_existing": skipped_existing,
        "embedded": embedded,
        "prompt_version": PROMPT_VERSION,
        "parse_warnings": parsed.warnings,
        "input_tokens": meter.totals.input_tokens,
        "output_tokens": meter.totals.output_tokens,
        **stats,
    }
    await events.emit(
        "agent_finished",
        agent_id=agent_id,
        status="succeeded",
        result=f"{len(fresh)} requirements ({summary['flagged']} flagged)",
        budget={},
    )
    return summary
