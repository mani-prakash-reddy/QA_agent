"""The project's reference documents, loaded for a run, and what they add to prompts and form rules."""

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlmodel import Session, select

from app.qa.models import FormField, ProjectReference
from app.qa.reference.api_spec import field_index, norm, related
from app.qa.reference.glossary import block


@dataclass
class Refs:
    terms: list[dict[str, Any]] = field(default_factory=list)
    operations: list[dict[str, Any]] = field(default_factory=list)
    _fields: dict[str, dict[str, Any]] | None = None

    @property
    def fields(self) -> dict[str, dict[str, Any]]:
        if self._fields is None:
            self._fields = field_index(self.operations)
        return self._fields

    def glossary_block(self, text: str) -> str:
        return block(self.terms, text) if self.terms else ""

    def api_block(self, text: str) -> str:
        ops = related(self.operations, text) if self.operations else []
        if not ops:
            return ""
        lines = []
        for o in ops:
            rules = []
            for f in o["body"][:12]:
                bits = [f["type"]] + (["required"] if f["required"] else [])
                for key, label in (
                    ("max_length", "max length"),
                    ("min_length", "min length"),
                    ("minimum", "min"),
                    ("maximum", "max"),
                ):
                    if f.get(key) is not None:
                        bits.append(f"{label} {f[key]}")
                if f.get("enum"):
                    bits.append("one of " + "/".join(map(str, f["enum"][:6])))
                rules.append(f"{f['name']} ({', '.join(bits)})")
            lines.append(
                f"- {o['method']} {o['path']}: {o['summary']}; inputs: {'; '.join(rules) or 'none'}; answers {', '.join(o['responses'])}"
            )
        return (
            "\nBackend contract of related API operations (use it ONLY to state expected outcomes and validations; "
            "steps still use only screens and labels from the cards):\n"
            + "\n".join(lines)
            + "\n"
        )

    def enrich(
        self, fields: list[FormField], about: str = ""
    ) -> tuple[list[FormField], dict[uuid.UUID, str]]:
        """Copies of the form fields with the rules the API specification adds (a maximum length the screen
        does not reveal, the allowed values). Only operations related to `about` (the requirement and the
        screen) are used: the e-mail of a sign-in form is not the e-mail of "add an employee".
        Returns (fields, {field id: operation it came from})."""
        out: list[FormField] = []
        source: dict[uuid.UUID, str] = {}
        index = (
            field_index(related(self.operations, about, 3)) if self.operations else {}
        )
        for f in fields:
            api = index.get(norm(f.label))
            if api is None:
                out.append(f)
                continue
            c = FormField(**f.model_dump())
            used = False
            if c.max_length is None and api.get("max_length"):
                c.max_length, used = int(api["max_length"]), True
            if c.min_length is None and api.get("min_length"):
                c.min_length, used = int(api["min_length"]), True
            if c.pattern is None and api.get("pattern"):
                c.pattern, used = str(api["pattern"]), True
            if c.min_value is None and api.get("minimum") is not None:
                c.min_value, used = str(api["minimum"]), True
            if c.max_value is None and api.get("maximum") is not None:
                c.max_value, used = str(api["maximum"]), True
            if not c.options and api.get("enum"):
                c.options, used = (
                    {"values": [str(v) for v in api["enum"]], "truncated": False},
                    True,
                )
            if api.get("required") and not c.required:
                c.required, used = True, True
            if used:
                source[c.id] = str(api["operation"])
            out.append(c)
        return out, source


def load_refs(session: Session, project_id: uuid.UUID) -> Refs:
    refs = Refs()
    for r in session.exec(
        select(ProjectReference).where(ProjectReference.project_id == project_id)
    ):
        if r.kind == "glossary":
            refs.terms = list(r.data.get("terms", []))
        elif r.kind == "api":
            refs.operations = list(r.data.get("operations", []))
    return refs


def load_refs_sync(project_id: uuid.UUID) -> Refs:
    from app.core.db import engine

    with Session(engine) as s:
        return load_refs(s, project_id)
