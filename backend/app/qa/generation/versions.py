"""What counts as the editable content of a test case (and so what a version stores)."""

from typing import Any

from app.qa.models import TestCase

EDITABLE = (
    "title",
    "preconditions",
    "steps",
    "rendered_steps",
    "test_data",
    "expected_result",
    "priority",
    "test_type",
    "role_name",
)


def version_content(t: TestCase) -> dict[str, Any]:
    return {k: getattr(t, k) for k in EDITABLE}
