"""Deterministic ambiguity checks. They do not depend on a model noticing vague wording."""

import re
from dataclasses import dataclass

# Words that make a requirement untestable as written.
VAGUE_TERMS = [
    "fast",
    "quick",
    "quickly",
    "slow",
    "easy",
    "easily",
    "simple",
    "simply",
    "user-friendly",
    "user friendly",
    "intuitive",
    "flexible",
    "robust",
    "scalable",
    "modern",
    "seamless",
    "efficient",
    "efficiently",
    "appropriate",
    "appropriately",
    "adequate",
    "adequately",
    "sufficient",
    "reasonable",
    "reasonably",
    "as needed",
    "as required",
    "as appropriate",
    "if necessary",
    "where possible",
    "as soon as possible",
    "asap",
    "minimal",
    "normally",
    "usually",
    "often",
    "several",
    "various",
    "etc",
    "and so on",
    "and/or",
    "pleasant",
    "responsive",
    "good",
    "better",
    "best",
    "nice",
    "smooth",
    "state-of-the-art",
]
VAGUE_RE = re.compile(
    r"(?<![\w-])(" + "|".join(re.escape(t) for t in VAGUE_TERMS) + r")(?![\w-])", re.I
)
PLACEHOLDER_RE = re.compile(
    r"\b(TBD|TBC|TODO|to be (?:determined|confirmed|defined|agreed)|will be agreed|XX+)\b|\[\s*\.{0,3}\s*\]",
    re.I,
)
MEASURABLE_RE = re.compile(r"\d")
QUALITY_RE = re.compile(
    r"\b(fast|quick\w*|slow|respon\w+|load\w*|perform\w*|available|availability|uptime|latency|"
    r"throughput|concurrent|secure|reliab\w+|usab\w+|easy|easily)\b",
    re.I,
)


@dataclass(frozen=True)
class Reason:
    source: str  # heuristic | llm
    reason: str
    severity: str = "blocking"  # blocking: needs a human decision; info: worth knowing

    def as_dict(self) -> dict[str, str]:
        return {"source": self.source, "reason": self.reason, "severity": self.severity}


def ambiguity_reasons(
    text: str, req_type: str, has_acceptance_criteria: bool
) -> list[Reason]:
    reasons: list[Reason] = []
    terms = sorted({m.group(1).lower() for m in VAGUE_RE.finditer(text)})
    if terms:
        reasons.append(
            Reason("heuristic", f"vague wording: {', '.join(repr(t) for t in terms)}")
        )
    if m := PLACEHOLDER_RE.search(text):
        reasons.append(Reason("heuristic", f"placeholder or open item: '{m.group(0)}'"))
    if req_type == "nfr" and QUALITY_RE.search(text) and not MEASURABLE_RE.search(text):
        reasons.append(
            Reason(
                "heuristic",
                "quality target has no measurable value (number, unit or threshold)",
            )
        )
    if req_type in {"functional", "business_rule"} and not has_acceptance_criteria:
        reasons.append(
            Reason(
                "heuristic",
                "no acceptance criteria stated for this requirement",
                "info",
            )
        )
    return reasons


def is_blocking(reasons: list[Reason] | list[dict[str, str]]) -> bool:
    return any(
        (r.severity if isinstance(r, Reason) else r.get("severity", "blocking"))
        == "blocking"
        for r in reasons
    )
