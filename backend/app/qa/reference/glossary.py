"""Business glossary: terms (with synonyms) the BRD and the application may use differently. Accepts CSV,
JSON, Markdown tables and "Term: definition" lines."""

import csv
import io
import json
import re
from typing import Any

MAX_TERMS = 500
MAX_DEF = 300


class ReferenceError(ValueError):
    pass


def _term(term: str, definition: str, synonyms: Any = None) -> dict[str, Any] | None:
    term = re.sub(r"[*_`]", "", str(term)).strip()
    definition = re.sub(r"\s+", " ", str(definition)).strip()
    if " | " in definition and not synonyms:  # "meaning | other name; another name"
        definition, _, synonyms = definition.partition(" | ")
    definition = definition.strip()[:MAX_DEF]
    if not term or len(term) > 80 or not definition:
        return None
    if isinstance(synonyms, str):
        synonyms = re.split(r"[;,|]", synonyms)
    syn = [str(s).strip() for s in (synonyms or []) if str(s).strip()]
    return {"term": term, "definition": definition, "synonyms": syn[:10]}


def parse_glossary(data: bytes, filename: str) -> list[dict[str, Any]]:
    text = data.decode("utf-8-sig", errors="replace")
    out: list[dict[str, Any]] = []
    stripped = text.lstrip()
    if stripped.startswith(("{", "[")):
        try:
            raw = json.loads(text)
        except ValueError as exc:
            raise ReferenceError("the glossary is not valid JSON") from exc
        items = (
            [{"term": k, "definition": v} for k, v in raw.items()]
            if isinstance(raw, dict)
            else raw
        )
        for it in items:
            if isinstance(it, dict):
                t = _term(
                    it.get("term", ""), it.get("definition", ""), it.get("synonyms")
                )
                if t:
                    out.append(t)
    elif filename.lower().endswith(".csv") or re.match(r"\s*term\s*,", text, re.I):
        rows = list(csv.reader(io.StringIO(text)))
        head = [h.strip().lower() for h in rows[0]] if rows else []
        has_head = "term" in head or "definition" in head
        for r in rows[1:] if has_head else rows:
            if len(r) >= 2:
                syn = r[2] if len(r) > 2 else None
                t = _term(r[0], r[1], syn)
                if t:
                    out.append(t)
    else:
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("|"):
                cells = [c.strip() for c in line.strip("|").split("|")]
                if (
                    len(cells) >= 2
                    and not re.fullmatch(r"[:\- ]+", cells[0])
                    and cells[0].lower() != "term"
                ):
                    t = _term(cells[0], cells[1], cells[2] if len(cells) > 2 else None)
                    if t:
                        out.append(t)
                continue
            m = re.match(
                r"^(?:[-*]\s+)?(?:\*\*)?([^:*]{1,80}?)(?:\*\*)?\s*(?::|\s[-–-]\s)\s*(.+)$",
                line,
            )
            if m:
                t = _term(m.group(1), m.group(2))
                if t:
                    out.append(t)
    if not out:
        raise ReferenceError(
            "no terms found (use 'Term: definition' lines, a Markdown table, CSV or JSON)"
        )
    return out[:MAX_TERMS]


def relevant(
    terms: list[dict[str, Any]], text: str, limit: int = 12
) -> list[dict[str, Any]]:
    """The glossary entries whose term (or a synonym) occurs in `text`."""
    low = text.lower()
    hits = []
    for t in terms:
        names = [t["term"], *t.get("synonyms", [])]
        if any(
            re.search(rf"(?<!\w){re.escape(n.lower())}(?!\w)", low) for n in names if n
        ):
            hits.append(t)
    return hits[:limit]


def block(terms: list[dict[str, Any]], text: str, limit: int = 12) -> str:
    """Prompt text for the terms used in `text` (empty when none are)."""
    hits = relevant(terms, text, limit)
    if not hits:
        return ""
    lines = [
        f"- {t['term']}: {t['definition']}"
        + (f" (also called: {', '.join(t['synonyms'])})" if t.get("synonyms") else "")
        for t in hits
    ]
    return (
        "\nGlossary of the business's own terms (use them to understand the text; do not invent others):\n"
        + "\n".join(lines)
        + "\n"
    )


def expansion(terms: list[dict[str, Any]], text: str) -> str:
    """Extra search words for retrieval: the synonyms of terms that occur in `text`."""
    return " ".join(
        s
        for t in relevant(terms, text, 30)
        for s in [t["term"], *t.get("synonyms", [])]
    )
