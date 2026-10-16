"""The test author: business-rule and end-to-end scenarios for one requirement, over its mapped screens.

Constrained like the mapping judge: steps may only use screens (aliases S1..Sn) and labels printed on
the cards, so a case that names a button that does not exist is dropped by code, not trusted. The
mechanical cases (field validation, boundaries, role access) come from rules; the author is asked
for what rules cannot know: the business rule, the end-to-end flow, the expected outcome."""

from typing import Any, Literal

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, Field

from app.qa.generation.rules import open_steps
from app.qa.generation.schema import CaseDraft, Step, Target, Value
from app.qa.mapping.cards import ScreenCard, norm_label

PROMPT_VERSION = "author-v3"

SYSTEM = """You write manual test cases for a web application from a business requirement and the
screens that implement it. Each screen card lists what was observed in the running application.

Rules:
- Use ONLY the screens and the labels printed on the cards. Every step's `screen` is a card alias
  (S1, S2...) and every `target` is copied EXACTLY from that card (a control, field or heading).
- Never click a control listed under "Destructive controls". Never invent a button, field or message.
- Cover what the requirement says that field-level checks cannot: the business rule, the main
  scenario, a meaningful negative, and an end-to-end flow across screens when the requirement spans
  more than one. Do NOT write per-field validation, boundary or role/permission cases: those are generated
  elsewhere. A case that opens a screen and verifies specific content is a valid display test.
- Steps: open_screen (target = the screen's heading or route), click (also for ticking a checkbox
  or opening a tab), fill (value), select, verify (expect = what the user should see). Finish with a verify step that states the outcome.
- Test data: use obviously fake values. For sign-in details or passwords use a placeholder such as
  <valid password>; never write a real-looking credential.
- expected_result must be observable on screen. If the requirement does not say what happens, say
  what the requirement implies and keep it generic rather than inventing messages or numbers.
- priority: P1 core flow, P2 important, P3 secondary. Write at most {limit} cases. If the cards
  show nothing that lets you test this requirement, return no cases."""

USER = """Requirement {key} ({rtype}): {title}
{description}
{criteria}
Roles in this application: {roles}

Screens:
{cards}
{feedback}{reviewer}"""


class AuthorStep(BaseModel):
    action: Literal["open_screen", "click", "fill", "select", "verify"]
    screen: str = Field(description="Card alias such as S1")
    target: str | None = None
    value: str | None = None
    value_is_placeholder: bool = False
    expect: str | None = None


class AuthorCase(BaseModel):
    title: str
    test_type: Literal["positive", "negative", "business_rule", "e2e"]
    priority: Literal["P1", "P2", "P3", "P4"] = "P2"
    role: str | None = None
    preconditions: list[str] = Field(default_factory=list)
    steps: list[AuthorStep]
    expected_result: str


class AuthorResult(BaseModel):
    cases: list[AuthorCase] = Field(default_factory=list)


def render_cards(cards: list[ScreenCard]) -> str:
    return "\n\n".join(f"[S{i}]\n{c.text}" for i, c in enumerate(cards, 1))


def build_messages(
    key: str,
    rtype: str,
    title: str,
    description: str,
    criteria: list[Any],
    roles: list[str],
    cards: list[ScreenCard],
    limit: int,
    feedback: str = "",
    reviewer_note: str = "",
) -> list[tuple[str, str]]:
    crit = (
        "Acceptance criteria:\n" + "\n".join(f"- {c}" for c in criteria)
        if criteria
        else ""
    )
    return [
        ("system", SYSTEM.format(limit=limit)),
        (
            "user",
            USER.format(
                key=key,
                rtype=rtype,
                title=title,
                description=description,
                criteria=crit,
                roles=", ".join(roles) or "(unknown)",
                cards=render_cards(cards),
                feedback=(
                    "\nFix these problems from your previous answer:\n" + feedback
                )
                if feedback
                else "",
                reviewer=(
                    "\nA reviewer read the previous cases for this requirement and asked: "
                    + reviewer_note
                )
                if reviewer_note
                else "",
            ),
        ),
    ]


async def author(
    model: BaseChatModel,
    key: str,
    rtype: str,
    title: str,
    description: str,
    criteria: list[Any],
    roles: list[str],
    cards: list[ScreenCard],
    limit: int,
    feedback: str = "",
    reviewer_note: str = "",
) -> AuthorResult:
    structured = model.with_structured_output(AuthorResult)
    result = await structured.ainvoke(
        build_messages(
            key,
            rtype,
            title,
            description,
            criteria,
            roles,
            cards,
            limit,
            feedback,
            reviewer_note,
        )
    )
    return (
        result
        if isinstance(result, AuthorResult)
        else AuthorResult.model_validate(result)
    )


def ground(
    result: AuthorResult,
    key: str,
    cards: list[ScreenCard],
    role_names: set[str],
    max_cases: int,
    start_title: str = "",
) -> tuple[list[CaseDraft], list[str]]:
    """Keep cases whose every step is grounded. Returns (drafts, problems) for feedback and metrics."""
    by_alias = {f"S{i}": c for i, c in enumerate(cards, 1)}
    drafts: list[CaseDraft] = []
    problems: list[str] = []
    for case in result.cases:
        steps: list[Step] = []
        bad: str | None = None
        needs_rows = False
        last_click = ""
        here: tuple[str, ...] | None = (
            None  # clicks that lead to where the case currently is
        )
        for st in case.steps:
            card = by_alias.get(st.screen.strip().upper())
            if card is None:
                bad = f"unknown screen '{st.screen}'"
                break
            if card.path is None:
                bad = f"screen {st.screen} ({card.display}) cannot be reached by any recorded path"
                break
            wanted = tuple(label for label, _ in card.path)
            if here is None or wanted[: len(here)] != here:
                steps += open_steps(
                    start_title, card.path
                )  # (re)start from the start page
            elif wanted != here:
                rest = card.path[len(here) :]
                if rest and last_click == norm_label(rest[0][0]):
                    rest = rest[
                        1:
                    ]  # the author already pressed the control that opens it
                steps += open_steps("", rest)[1:]  # continue from here
            needs_rows = needs_rows or card.needs_rows
            here = wanted
            if st.action == "open_screen":
                continue
            if st.action == "verify":
                if not (st.expect or "").strip():
                    bad = "a verify step has no expectation"
                    break
                steps.append(Step(action="verify", expect=st.expect.strip()))  # type: ignore[union-attr]
                continue
            label = norm_label(st.target or "")
            if not label or label not in card.labels:
                bad = f"'{st.target}' is not on screen {st.screen} ({card.display})"
                break
            # A heading or title is on the card but is not something a tester can press or type in.
            kind = card.kinds.get(label)
            fieldlike = {
                "field",
                "input",
                "combobox",
                "textarea",
                "select",
                "checkbox",
                "radio",
            }
            if kind is None or (
                st.action in {"fill", "select"} and kind not in fieldlike
            ):
                bad = f"'{st.target}' on {st.screen} is not a control ({card.display})"
                break
            if label in card.destructive:
                bad = f"'{st.target}' is a destructive control"
                break
            needs_rows = needs_rows or label in card.row_labels
            value = None
            if st.action in {"fill", "select"}:
                if st.value is None:
                    bad = f"step on '{st.target}' has no value"
                    break
                value = Value(
                    kind="placeholder" if st.value_is_placeholder else "literal",
                    text=st.value.strip("<>"),
                )
            kind = "field" if st.action in {"fill", "select"} else kind
            steps.append(
                Step(
                    action=st.action,
                    target=Target(label=(st.target or "").strip(), kind=kind),
                    value=value,
                )
            )
            last_click = label if st.action == "click" else ""
        if bad is None:
            # judged on what the author wrote, not on the navigation clicks added for reachability
            actions = [
                s for s in case.steps if s.action not in {"open_screen", "verify"}
            ]
            checks = [s for s in case.steps if s.action == "verify"]
            if not actions and not checks:
                bad = "it neither interacts with the screen nor checks anything on it"
            elif not case.expected_result.strip():
                bad = "no expected result"
        if bad is not None:
            problems.append(f"'{case.title}': {bad}")
            continue
        used = [
            by_alias[s.screen.strip().upper()]
            for s in case.steps
            if s.screen.strip().upper() in by_alias
        ]
        first = used[0] if used else cards[0]
        role = case.role if case.role in role_names else None
        pre = list(case.preconditions)
        if first.module == "Sign in":
            # a case that starts on the sign-in screen starts as a visitor who is not signed in
            role = None
            pre = [
                "The tester is signed out (a visitor, not signed in)",
                *[x for x in pre if "signed in as" not in x],
            ]
        if needs_rows:
            pre.append(
                "At least one row exists in the list (a step uses a per-row control): create one first if it is empty"
            )
        if role:
            pre.insert(
                0,
                f"The tester is signed in as the '{role}' role (use that role's test account)",
            )
        drafts.append(
            CaseDraft(
                requirement_key=key,
                title=case.title.strip()[:480],
                test_type=case.test_type,
                priority=case.priority,
                role_name=role,
                module=first.module,
                state_id=first.state_id,
                preconditions=pre,
                steps=steps,
                expected_result=case.expected_result.strip(),
                origin="llm",
                evidence={
                    "basis": "requirement",
                    "rule": "author",
                    "screens": [c.display for c in used][:4],
                    **({"needs_rows": True} if needs_rows else {}),
                },
            )
        )
        if len(drafts) >= max_cases:
            break
    return drafts, problems
