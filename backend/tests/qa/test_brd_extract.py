import pytest
from langchain_core.messages import AIMessage

from app.qa.brd.chunk import Chunk
from app.qa.brd.extract import (
    Candidate,
    ExtractedRequirement,
    ExtractionResult,
    assign_keys,
    extract_chunk,
    finalize_reasons,
    ground_candidates,
    ground_quote,
    merge_duplicates,
    normalize,
)
from app.qa.brd.heuristics import ambiguity_reasons, is_blocking
from app.qa.llm.fake import ScriptedChatModel

SOURCE = (
    "FR-1: A visitor shall be able to register a customer account by providing an email address.\n\n"
    "FR-12: The checkout process must be user-friendly.\n\n"
    "NFR-3: The password policy is TBD and will be agreed with the security team."
)


def chunk(text: str = SOURCE) -> Chunk:
    return Chunk(0, "3.1 Accounts", 2, 2, text, "text")


def req(**kw) -> ExtractedRequirement:  # type: ignore[no-untyped-def]
    base = {
        "title": "T",
        "description": "D",
        "quote": "A visitor shall be able to register a customer account",
    }
    return ExtractedRequirement(**{**base, **kw})


# ------------------------------------------------------------------ grounding
def test_exact_quote_is_kept_with_the_models_wording() -> None:
    q = "A visitor shall be able to register a customer account by providing an email address."
    assert ground_quote(q, SOURCE) == q


def test_quote_matching_ignores_case_whitespace_and_typographic_characters() -> None:
    src = "The user’s password – minimum 8 characters   must be   hidden."
    assert (
        ground_quote("the user's password - minimum 8 characters must be hidden.", src)
        is not None
    )


def test_a_near_miss_is_replaced_by_the_documents_own_sentence() -> None:
    # the model dropped a word: we store what the document really says
    got = ground_quote(
        "A visitor shall be able to register customer account by providing an email address.",
        SOURCE,
    )
    assert got is not None and "register a customer account" in got


@pytest.mark.parametrize(
    "invented",
    [
        "The system shall send a confirmation SMS after registration.",
        "Users must verify their phone number within 24 hours.",
        "short",
        "",
    ],
)
def test_an_invented_or_empty_quote_is_rejected(invented: str) -> None:
    assert ground_quote(invented, SOURCE) is None


def test_ground_candidates_counts_rejections() -> None:
    result = ExtractionResult(
        requirements=[
            req(),
            req(
                title="Hallucinated",
                quote="The system shall mine cryptocurrency while idle.",
            ),
            req(
                title="", quote="A visitor shall be able to register a customer account"
            ),
        ]
    )
    accepted, rejected = ground_candidates(chunk(), result)
    assert len(accepted) == 1 and rejected == 2


# ------------------------------------------------------------------ dedupe + ids
def cand(native: str | None, quote: str, **kw) -> Candidate:  # type: ignore[no-untyped-def]
    return Candidate(
        chunk=chunk(), data=req(native_id=native, quote=quote, **kw), quote=quote
    )


def test_duplicates_are_merged_by_id_or_quote_and_criteria_are_combined() -> None:
    a = cand("FR-1", "Quote one is here.", acceptance_criteria=["x"])
    b = cand("fr-1", "Different text entirely here.", acceptance_criteria=["y"])
    c = cand(None, "Quote one is here.")
    merged = merge_duplicates([a, b, c])
    assert len(merged) == 1 and merged[0].data.acceptance_criteria == ["x", "y"]


def test_brd_ids_are_kept_and_the_rest_are_numbered_in_order() -> None:
    cs = [
        cand("FR-9", "q nine here."),
        cand(None, "q two here."),
        cand("BR 1", "q three here."),
        cand("not an id!", "q four here."),
    ]
    assign_keys(cs, existing_keys=set())
    assert [(c.key, c.key_origin) for c in cs] == [
        ("FR-9", "brd"),
        ("REQ-001", "generated"),
        ("BR-1", "brd"),
        ("REQ-002", "generated"),
    ]


def test_ids_never_collide_with_existing_project_requirements() -> None:
    cs = [cand("FR-1", "q one is here."), cand(None, "q two is here.")]
    assign_keys(cs, existing_keys={"FR-1", "REQ-001", "REQ-007"})
    assert cs[0].key == "REQ-008" and cs[0].key_origin == "generated"  # FR-1 was taken
    assert cs[1].key == "REQ-009"
    assert any("already used" in r.reason for r in cs[0].reasons)


# ------------------------------------------------------------------ ambiguity
@pytest.mark.parametrize(
    "text",
    [
        "The system should respond quickly to search requests.",
        "The checkout process must be user-friendly.",
        "The password policy is TBD.",
        "Error messages shall be shown as appropriate.",
        "Passwords will be agreed later [ ].",
    ],
)
def test_vague_or_open_wording_is_flagged_as_blocking(text: str) -> None:
    assert is_blocking(ambiguity_reasons(text, "functional", True))


@pytest.mark.parametrize(
    "text",
    [
        "The catalogue shall load within 3 seconds on a standard broadband connection.",
        "A basket line quantity must be at least 1 and at most 5.",
        "Passwords shall be between 8 and 128 characters long.",
        "The system shall be available 99.5% of the time during business hours.",
    ],
)
def test_measurable_requirements_are_not_flagged(text: str) -> None:
    kind = "nfr" if "available" in text or "load" in text else "validation"
    assert not is_blocking(ambiguity_reasons(text, kind, True))


def test_an_nfr_without_a_number_is_flagged_but_a_missing_acceptance_criterion_is_only_info() -> (
    None
):
    reasons = ambiguity_reasons(
        "The application must handle many concurrent users reliably.", "nfr", False
    )
    assert is_blocking(reasons) and any("measurable" in r.reason for r in reasons)
    info = ambiguity_reasons(
        "A user shall log in with a password.", "functional", False
    )
    assert info and not is_blocking(info) and info[0].severity == "info"


def test_finalize_combines_heuristic_and_model_findings_without_duplicates() -> None:
    c = cand(
        "FR-12",
        "The checkout process must be user-friendly.",
        ambiguous=True,
        ambiguity_reasons=["'user-friendly' is not measurable"],
        description="Checkout must be user-friendly",
    )
    assert finalize_reasons(c) is True
    sources = {r.source for r in c.reasons}
    assert sources == {"heuristic", "llm"}
    clean = cand(
        "FR-1",
        "A visitor shall be able to register a customer account",
        description="Register an account",
    )
    clean.data.acceptance_criteria = ["email must be valid"]
    assert finalize_reasons(clean) is False


# ------------------------------------------------------------------ the LLM call
@pytest.mark.anyio
async def test_extract_chunk_parses_structured_output_from_the_model() -> None:
    call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "ExtractionResult",
                "id": "1",
                "args": {
                    "requirements": [
                        {
                            "native_id": "FR-1",
                            "type": "functional",
                            "title": "Register account",
                            "description": "A visitor can register.",
                            "actors": ["Visitor"],
                            "quote": "A visitor shall be able to register a customer account by providing an email address.",
                        }
                    ]
                },
            }
        ],
    )
    result = await extract_chunk(ScriptedChatModel(script=[call]), chunk())
    assert [r.native_id for r in result.requirements] == ["FR-1"]
    assert result.requirements[0].actors == ["Visitor"]


def test_normalize_is_stable() -> None:
    assert normalize("  A B – c ") == "a b - c"
