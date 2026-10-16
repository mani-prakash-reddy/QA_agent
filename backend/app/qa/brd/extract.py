"""LLM extraction of requirements from chunks, with grounding against the source text."""

import difflib
import re
from dataclasses import dataclass, field
from typing import Literal

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, Field

from app.qa.brd.chunk import Chunk
from app.qa.brd.heuristics import Reason, ambiguity_reasons, is_blocking
from app.qa.brd.prompts import EXTRACT_SYSTEM, EXTRACT_USER

ReqType = Literal[
    "functional", "business_rule", "validation", "acceptance_criterion", "nfr"
]
NATIVE_ID_RE = re.compile(r"^[A-Za-z]{1,8}[-_.]?\d+(?:[.\-]\d+)*$")
FUZZY_THRESHOLD = 0.88


class ExtractedRequirement(BaseModel):
    native_id: str | None = None
    type: ReqType = "functional"
    title: str = Field(max_length=300)
    description: str
    actors: list[str] = Field(default_factory=list)
    module: str | None = None
    acceptance_criteria: list[str] = Field(default_factory=list)
    quote: str
    ambiguous: bool = False
    ambiguity_reasons: list[str] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    requirements: list[ExtractedRequirement] = Field(default_factory=list)


@dataclass
class Candidate:
    """A requirement that survived grounding: its quote is real text from the document."""

    chunk: Chunk
    data: ExtractedRequirement
    quote: str  # the document's own words, not the model's copy
    key: str = ""
    key_origin: str = "generated"
    reasons: list[Reason] = field(default_factory=list)


def normalize(text: str) -> str:
    text = (
        text.replace("’", "'")
        .replace("‘", "'")
        .replace("“", '"')
        .replace("”", '"')
        .replace("–", "-")
        .replace("-", "-")
        .replace(" ", " ")
    )
    return re.sub(r"\s+", " ", text).strip().lower()


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip(" |-") for p in parts if len(p.strip(" |-")) > 8]


def ground_quote(quote: str, source: str) -> str | None:
    """Return the real text in `source` that the quote refers to, or None if it is not there.

    Exact match (ignoring case/whitespace/typographic quotes) keeps the model's wording;
    a close sentence match returns the document's sentence instead. Anything else is rejected,
    which is what stops a paraphrased or invented requirement from entering the model."""
    q = normalize(quote)
    if len(q) < 8:
        return None
    if q in normalize(source):
        return quote.strip()
    best, best_ratio = None, 0.0
    for s in _sentences(source):
        ratio = difflib.SequenceMatcher(None, q, normalize(s)).ratio()
        if ratio > best_ratio:
            best, best_ratio = s, ratio
    return best if best is not None and best_ratio >= FUZZY_THRESHOLD else None


async def extract_chunk(
    model: BaseChatModel, chunk: Chunk, context: str = ""
) -> ExtractionResult:
    structured = model.with_structured_output(ExtractionResult)
    result = await structured.ainvoke(
        [
            ("system", EXTRACT_SYSTEM),
            (
                "user",
                EXTRACT_USER.format(
                    section=chunk.section_path or "(top level)", text=chunk.text
                )
                + context,
            ),
        ]
    )
    if isinstance(result, ExtractionResult):
        return result
    return ExtractionResult.model_validate(result)


def ground_candidates(
    chunk: Chunk, result: ExtractionResult
) -> tuple[list[Candidate], int]:
    """Keep requirements whose quote exists in the chunk. Returns (accepted, rejected_count)."""
    accepted: list[Candidate] = []
    rejected = 0
    for item in result.requirements:
        real = ground_quote(item.quote, chunk.text)
        if real is None or not item.title.strip() or not item.description.strip():
            rejected += 1
            continue
        accepted.append(Candidate(chunk=chunk, data=item, quote=real))
    return accepted, rejected


def merge_duplicates(candidates: list[Candidate]) -> list[Candidate]:
    """Overlapping chunks can yield the same requirement twice: same native id, or same quote."""
    seen_ids: dict[str, Candidate] = {}
    seen_quotes: dict[str, Candidate] = {}
    out: list[Candidate] = []
    for c in candidates:
        nid = (c.data.native_id or "").strip().upper()
        q = normalize(c.quote)
        twin = (seen_ids.get(nid) if nid else None) or seen_quotes.get(q)
        if twin is not None:
            for crit in c.data.acceptance_criteria:
                if crit not in twin.data.acceptance_criteria:
                    twin.data.acceptance_criteria.append(crit)
            continue
        if nid:
            seen_ids[nid] = c
        seen_quotes[q] = c
        out.append(c)
    return out


def assign_keys(candidates: list[Candidate], existing_keys: set[str]) -> None:
    """BRD-native ids are kept; everything else gets REQ-001, REQ-002, ... in document order."""
    used = set(existing_keys)
    next_n = 1 + max(
        (int(m.group(1)) for k in used if (m := re.fullmatch(r"REQ-(\d+)", k))),
        default=0,
    )
    for c in candidates:
        raw = (c.data.native_id or "").strip()
        key = re.sub(r"\s+", "-", raw).upper()
        if key and NATIVE_ID_RE.match(key) and key not in used:
            c.key, c.key_origin = key, "brd"
        else:
            if key and NATIVE_ID_RE.match(key):  # a real id that is already taken
                c.reasons.append(
                    Reason(
                        "heuristic",
                        f"id '{key}' is already used by another requirement",
                        "info",
                    )
                )
            while f"REQ-{next_n:03d}" in used:
                next_n += 1
            c.key, c.key_origin = f"REQ-{next_n:03d}", "generated"
            next_n += 1
        used.add(c.key)


def finalize_reasons(c: Candidate) -> bool:
    """Combine heuristic and model ambiguity findings. Returns the blocking flag."""
    text = f"{c.data.title}. {c.data.description} {c.quote}"
    reasons = list(c.reasons)
    reasons += ambiguity_reasons(text, c.data.type, bool(c.data.acceptance_criteria))
    if c.data.ambiguous:
        for why in c.data.ambiguity_reasons or [
            "the model judged the wording ambiguous"
        ]:
            reasons.append(Reason("llm", why))
    # one entry per distinct reason text
    unique: dict[str, Reason] = {}
    for r in reasons:
        unique.setdefault(r.reason, r)
    c.reasons = list(unique.values())
    return is_blocking(c.reasons)
