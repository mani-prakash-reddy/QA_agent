"""Action classifier: decides whether a UI element may be clicked in safe mode."""

import re
from dataclasses import dataclass, field

DEFAULT_DENY_PATTERNS = [
    r"\bdelet(e|ing)\b",
    r"\bremove\b",
    r"\bdestroy\b",
    r"\bdeactivate\b",
    r"\blog ?out\b",
    r"\bsign ?out\b",
    r"^pay\b",
    r"\bpay now\b",
    r"\b(make|submit|confirm) (a |the )?payment\b",
    r"\bpurchase\b",
    r"\bplace order\b",
    r"\bcheckout\b",
    r"\bconfirm order\b",
    r"\bsubmit order\b",
    r"\bterminate\b",
    r"\bwipe\b",
    r"\breset (all|data)\b",
]
DESTRUCTIVE_HREF = re.compile(r"/(log-?out|sign-?out|delete|remove)\b", re.I)


@dataclass(frozen=True)
class Verdict:
    blocked: bool
    reason: str | None = None


@dataclass
class SafetyPolicy:
    allow_destructive: bool = False
    allowlist: list[str] = field(
        default_factory=list
    )  # regexes of labels explicitly permitted
    extra_deny: list[str] = field(default_factory=list)

    def classify(self, label: str, href: str | None = None) -> Verdict:
        if self.allow_destructive:
            return Verdict(False)
        text = label.strip().lower()
        if any(re.search(p, text, re.I) for p in self.allowlist):
            return Verdict(False)
        for pattern in [*DEFAULT_DENY_PATTERNS, *self.extra_deny]:
            if re.search(pattern, text, re.I):
                return Verdict(True, f"denylist:{pattern}")
        if href and DESTRUCTIVE_HREF.search(href):
            return Verdict(True, f"denylist:href:{href}")
        return Verdict(False)
