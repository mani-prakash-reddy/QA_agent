"""The mapping judge: one structured-output call per requirement over a fixed list of candidate screens.

Constrained by construction: the model may only pick candidate aliases (S1..Sn), and every piece of
evidence it cites must be a label that exists on that screen's card. Unverifiable claims lower
confidence rather than being trusted."""

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, Field

from app.qa.mapping.cards import norm_label
from app.qa.mapping.retrieve import Candidate

PROMPT_VERSION = "map-v3"
MIN_STORED = 0.3  # below this a verdict is noise and is dropped
UNVERIFIED_CAP = 0.4  # no checkable evidence -> never a confident match
MAX_SCREENS = 4  # a requirement rarely lives on more screens than this
SPREAD = 0.2  # keep verdicts within this distance of the best one

SYSTEM = """You decide whether an existing web application implements a business requirement.
You are shown the requirement and a few candidate screens. Each screen card lists only what was
observed in the running application: headings, controls, form fields and their rules.

Rules:
- Judge ONLY from the cards. Never assume a feature exists because it is common.
- A screen implements the requirement only if the card shows concrete UI for it (a field, control,
  heading or rule that corresponds to what the requirement asks). Similar vocabulary is not enough.
- Controls reveal the feature: "Add to Basket", "Click for more information about the product" and
  paging controls together show a product catalogue; a search box in the shell shows search exists.
  "Shell controls" are the shared toolbar/menu. If a requirement is met only by a shell control,
  return ONE verdict, for the screen whose own purpose is closest (the home or search screen for
  search), not one per screen.
- For each candidate that plausibly implements it, give confidence 0-1:
  0.85+ = the card shows the specific behaviour or fields the requirement names;
  0.6-0.85 = clearly the right screen but part of the requirement is not visible;
  0.3-0.6 = related screen, weak evidence.
  Omit candidates that do not implement it. A screen that shows PART of the requirement is a
  match at lower confidence (0.5-0.7): do not return nothing just because some details (a price,
  a column, a validation rule) are not visible on the card. Return nothing only when no candidate
  shows any concrete UI for what the requirement asks.
- evidence: copy 1-4 labels EXACTLY as they appear in the card (a heading, control or field label).
- rationale: one short sentence a reviewer can check against the card.
- ui_observable=false ONLY for requirements that cannot be seen in a UI at all (performance targets,
  availability, encryption, backend-only rules, audit logging). A requirement about what a screen
  shows or lets a user do is observable even if you cannot find it: then return no verdicts.
- If no candidate implements it, return an empty verdicts list: that is a valid, useful answer."""

USER = """Requirement {key} ({rtype}): {title}
{description}
{criteria}
Candidate screens:
{cards}"""


class Verdict(BaseModel):
    screen: str = Field(description="Candidate alias such as S2")
    confidence: float = Field(ge=0, le=1)
    rationale: str
    evidence: list[str] = Field(default_factory=list)


class JudgeResult(BaseModel):
    ui_observable: bool = True
    verdicts: list[Verdict] = Field(default_factory=list)


class Judged(BaseModel):
    """A verified verdict, resolved to a real screen."""

    group_key: str
    confidence: float
    rationale: str
    citations: list[dict[str, str]]
    verified: bool
    retrieval_score: float


def render_cards(candidates: list[Candidate]) -> str:
    return "\n\n".join(f"[S{i}]\n{c.card.text}" for i, c in enumerate(candidates, 1))


def build_messages(
    key: str,
    rtype: str,
    title: str,
    description: str,
    criteria: list[Any],
    candidates: list[Candidate],
) -> list[tuple[str, str]]:
    crit = (
        "Acceptance criteria:\n" + "\n".join(f"- {c}" for c in criteria)
        if criteria
        else ""
    )
    return [
        ("system", SYSTEM),
        (
            "user",
            USER.format(
                key=key,
                rtype=rtype,
                title=title,
                description=description,
                criteria=crit,
                cards=render_cards(candidates),
            ),
        ),
    ]


SECOND_LOOK = (
    "user",
    "Look again before answering. Your first pass found no implementing screen. Check each card "
    "for ANY control, field or heading that corresponds to part of the requirement. Return it at "
    "lower confidence if one exists; return nothing only if truly none does.",
)


async def judge(
    model: BaseChatModel,
    key: str,
    rtype: str,
    title: str,
    description: str,
    criteria: list[Any],
    candidates: list[Candidate],
    *,
    second_look: bool = False,
) -> JudgeResult:
    structured = model.with_structured_output(JudgeResult)
    messages = build_messages(key, rtype, title, description, criteria, candidates)
    if second_look:
        messages.append(SECOND_LOOK)
    result = await structured.ainvoke(messages)
    return (
        result
        if isinstance(result, JudgeResult)
        else JudgeResult.model_validate(result)
    )


def verify(result: JudgeResult, candidates: list[Candidate]) -> list[Judged]:
    """Resolve aliases, keep only verifiable evidence, and cap confidence when there is none."""
    by_alias = {f"S{i}": c for i, c in enumerate(candidates, 1)}
    out: dict[str, Judged] = {}
    for v in result.verdicts:
        cand = by_alias.get(v.screen.strip().upper())
        if cand is None:  # invented screen: ignore
            continue
        card_text = norm_label(cand.card.text)
        cites = []
        for e in v.evidence:
            n = norm_label(e)
            if n and (n in cand.card.labels or n in card_text):
                cites.append({"label": e.strip()})
        confidence = min(max(v.confidence, 0.0), 1.0)
        verified = bool(cites)
        if not verified:
            confidence = min(confidence, UNVERIFIED_CAP)
        if confidence < MIN_STORED:
            continue
        prev = out.get(cand.card.group_key)
        if prev is None or confidence > prev.confidence:
            out[cand.card.group_key] = Judged(
                group_key=cand.card.group_key,
                confidence=round(confidence, 3),
                rationale=v.rationale.strip()[:600],
                citations=cites,
                verified=verified,
                retrieval_score=cand.score,
            )
    ranked = sorted(out.values(), key=lambda j: -j.confidence)
    if not ranked:
        return ranked
    best = ranked[0].confidence
    return [j for j in ranked if j.confidence >= best - SPREAD][:MAX_SCREENS]
