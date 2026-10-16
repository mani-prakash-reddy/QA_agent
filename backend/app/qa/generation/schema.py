"""Structured test steps and drafts. Steps reference real UI labels; text is rendered from them."""

import hashlib
import re
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

Action = Literal["open_screen", "click", "fill", "clear", "select", "check", "verify"]
ValueKind = Literal["literal", "repeat", "placeholder", "empty"]
Basis = Literal["observed", "inferred", "requirement", "not_observed"]


class Value(BaseModel):
    kind: ValueKind = "literal"
    text: str = ""  # literal value, or the placeholder name
    char: str = "a"  # repeat
    count: int = 0  # repeat

    def render(self) -> str:
        if self.kind == "empty":
            return "nothing (leave it empty)"
        if self.kind == "repeat":
            return f'a string of {self.count} characters ("{self.char}" repeated)'
        if self.kind == "placeholder":
            return f"<{self.text}>"
        return f'"{self.text}"'

    def short(self) -> str:
        if self.kind == "empty":
            return "(empty)"
        if self.kind == "repeat":
            return f"{self.char} × {self.count}"
        if self.kind == "placeholder":
            return f"<{self.text}>"
        return self.text


class Target(BaseModel):
    label: str
    kind: str = ""  # field | button | link | tab | screen


class Step(BaseModel):
    action: Action
    target: Target | None = None
    value: Value | None = None
    screen: str | None = (
        None  # open_screen: the navigation path, e.g. "Address > My saved addresses"
    )
    expect: str | None = None  # verify


def render_step(step: Step) -> str:
    label = step.target.label if step.target else ""
    kind = step.target.kind if step.target and step.target.kind else ""
    noun = (
        f" {kind}" if kind in {"button", "link", "tab", "checkbox", "menuitem"} else ""
    )
    match step.action:
        case "open_screen":
            if step.screen and step.screen.startswith(("/", "#/")):
                return f"Open the page '{step.screen}' of the application"
            return (
                f"Open the application at its start page ({step.screen})"
                if step.screen
                else "Open the application"
            )
        case "click":
            return f"Click the '{label}'{noun}"
        case "fill":
            val = step.value.render() if step.value else "a value"
            return f"Enter {val} in the '{label}' field"
        case "clear":
            return f"Clear the '{label}' field"
        case "select":
            val = step.value.render() if step.value else "an option"
            return f"Select {val} in the '{label}' field"
        case "check":
            return f"Tick the '{label}' checkbox"
        case "verify":
            return f"Verify that {step.expect}"


class CaseDraft(BaseModel):
    requirement_key: str
    title: str
    test_type: str
    priority: str = "P3"
    role_name: str | None = None
    module: str | None = None
    state_id: uuid.UUID | None = None
    preconditions: list[str] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=list)
    expected_result: str = ""
    origin: Literal["rule", "llm"] = "rule"
    evidence: dict[str, Any] = Field(default_factory=dict)

    def test_data(self) -> list[dict[str, str]]:
        return [
            {"field": s.target.label, "value": s.value.short(), "kind": s.value.kind}
            for s in self.steps
            if s.action in {"fill", "select"} and s.target and s.value
        ]

    def rendered(self) -> list[str]:
        return [f"{i}. {render_step(s)}" for i, s in enumerate(self.steps, 1)]

    def signature(self) -> str:
        # not the state id: two recorded variants of one page that a tester reaches and checks the same
        # way are one test
        parts = [self.test_type, self.role_name or ""]
        for s in self.steps:
            parts.append(
                "|".join(
                    [
                        s.action,
                        (s.target.label if s.target else "").lower(),
                        s.value.short().lower() if s.value else "",
                        (s.screen or "").lower(),
                    ]
                )
            )
        parts.append(re.sub(r"\W+", " ", self.expected_result.lower()).strip())
        return hashlib.sha1("\n".join(parts).encode()).hexdigest()  # noqa: S324 (not security)
