import pytest

from app.qa.browser.extract import (
    Element,
    Snapshot,
    clean_label,
    compute_fingerprint,
    mask_pii,
    route_path,
    route_template,
    route_title,
)
from app.qa.crawler.session import is_section_label


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://x/items", "/items"),
        ("http://x/items/123", "/items/:id"),
        ("http://x/items/8f14e45f-ceea-467a-9575-0e3b1f0b1c11/edit", "/items/:id/edit"),
        ("http://x/items?page=2&sort=a", "/items?page&sort"),
        ("http://x/items?b=1&a=2", "/items?a&b"),  # order-insensitive
        ("http://x/", "/"),
        ("http://x/#/search", "#/search"),  # hash-routed SPA (Angular)
        ("http://x/#/product/42?tab=x", "#/product/:id?tab"),
        ("http://x/page#section", "/page"),  # a plain anchor is not a route
    ],
)
def test_route_template(url: str, expected: str) -> None:
    assert route_template(url) == expected


def test_route_path_for_hash_routes() -> None:
    assert route_path("http://x/#/login") == "/login"
    assert route_path("http://x/app#/a/b?q=1") == "/a/b"
    assert route_path("http://x/plain") == "/plain"


@pytest.mark.parametrize(
    ("raw", "label", "required"),
    [
        ("Title *", "Title", True),
        ("Title*", "Title", True),
        ("Email", "Email", False),
        ("A * B", "A * B", False),
    ],
)
def test_clean_label(raw: str, label: str, required: bool) -> None:
    assert clean_label(raw) == (label, required)


def snap(
    heading: str = "Dashboard",
    headings: list[str] | None = None,
    labels: list[str] | None = None,
    in_row: bool = False,
    fp_ignore: str = "",
) -> Snapshot:
    els = [
        Element(h=f"e{i}", kind="button", label=lbl, region="main", in_row=in_row)
        for i, lbl in enumerate(labels or ["Add"])
    ]
    s = Snapshot(
        url="http://x/",
        title="t",
        heading=heading,
        headings=headings or [heading],
        overlay=None,
        elements=els,
        forms=[],
        row_count=0,
        route="/",
        fp_ignore=fp_ignore,
    )
    return s


def test_fingerprint_ignores_user_specific_h1() -> None:
    a = snap("Hi, ana@x.com")
    b = snap("Hi, Bob")
    assert compute_fingerprint(a) == compute_fingerprint(b)


def test_fingerprint_distinguishes_panels_by_subheading_and_controls() -> None:
    base = compute_fingerprint(snap("Items", ["Items"], ["Add"]))
    assert base != compute_fingerprint(
        snap("Items", ["Items", "Archived items"], ["Add"])
    )
    assert base != compute_fingerprint(snap("Items", ["Items"], ["Add", "Export"]))


def test_fingerprint_ignores_data_rows_and_shell_toggles() -> None:
    base = compute_fingerprint(
        snap("Items", ["Items"], ["Add", "Collapse Sidebar"], fp_ignore="sidebar")
    )
    assert base == compute_fingerprint(
        snap("Items", ["Items"], ["Add", "Open Sidebar"], fp_ignore="sidebar")
    )
    with_rows = snap("Items", ["Items"], ["Add"])
    with_rows.elements.append(
        Element(h="r", kind="button", label="Row actions", region="main", in_row=True)
    )
    assert compute_fingerprint(with_rows) == compute_fingerprint(
        snap("Items", ["Items"], ["Add"])
    )


def test_global_overlay_fingerprint_is_route_independent() -> None:
    overlay = {"role": "menu", "name": "Account menu"}
    a, b = snap(), snap()
    a.overlay = b.overlay = overlay
    a.route, b.route = "/", "/items"
    assert compute_fingerprint(a) != compute_fingerprint(b)
    assert compute_fingerprint(a, include_route=False) == compute_fingerprint(
        b, include_route=False
    )


@pytest.mark.parametrize(
    ("raw", "masked"),
    [
        ("Your Basket (admin@juice-sh.op)", "Your Basket (<email>)"),
        ("Hi, ana.b+x@mail.example.com", "Hi, <email>"),
        ("No email here", "No email here"),
    ],
)
def test_mask_pii(raw: str, masked: str) -> None:
    assert mask_pii(raw) == masked


@pytest.mark.parametrize(
    ("url", "title"),
    [
        ("http://x/#/privacy-security/change-password", "Change Password"),
        ("http://x/dataerasure", "Dataerasure"),
        ("http://x/items/123", "Items"),
        ("http://x/", ""),
    ],
)
def test_route_title(url: str, title: str) -> None:
    assert route_title(url) == title


@pytest.mark.parametrize(
    ("label", "ok"),
    [
        ("Items", True),
        ("Order History", True),
        ("Back", False),
        ("back to homepage", False),
        ("Close", False),
        ("", False),
        ("Go back", False),
    ],
)
def test_navigation_labels_that_can_name_a_module(label: str, ok: bool) -> None:
    assert is_section_label(label) is ok


def test_mask_content_drops_data_and_masks_identifiers() -> None:
    from app.qa.browser.extract import mask_content

    out = mask_content(
        [["Product", "Qty 2", "Price"]],
        [
            "Total Price:",
            "3 items in your basket",  # data: digits, no label colon
            "Signed in as ana@example.com",
            "Order id: 12345",
            "Welcome back",
            "Welcome back",
            "token " + "a" * 30,
        ],
    )
    assert out["headers"] == [["Product", "Qty #", "Price"]]
    assert "Total Price:" in out["text"]
    assert "3 items in your basket" not in out["text"]
    assert out["text"].count("Welcome back") == 1
    assert not any("Order id" in t for t in out["text"])
    assert "Signed in as <email>" in out["text"]
    assert not any("ana@" in t or "a" * 24 in t for t in out["text"])
