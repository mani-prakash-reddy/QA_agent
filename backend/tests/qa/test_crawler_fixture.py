"""End-to-end crawler test: real Chromium + Postgres + Redis against the fixture SPA (no LLM)."""

import pytest
from sqlmodel import Session, col, select

from app.core.db import engine
from app.qa.models import (
    AgentEvent,
    AppSnapshot,
    CrawlEvent,
    FormField,
    RoleStateAccess,
    Run,
    Transition,
    UiElement,
    UiForm,
    UiState,
)
from tests.qa.conftest import Crawl
from tests.qa.fixture_app import USERS

pytestmark = pytest.mark.filterwarnings("ignore::ResourceWarning")


def states(c: Crawl) -> list[UiState]:
    with Session(engine) as s:
        return list(
            s.exec(select(UiState).where(UiState.snapshot_id == c.snapshot_id)).all()
        )


def test_run_succeeds_and_snapshot_ready(crawl: Crawl) -> None:
    assert crawl.status == "succeeded"
    with Session(engine) as s:
        snap = s.get(AppSnapshot, crawl.snapshot_id)
        run = s.get(Run, crawl.run_id)
        assert snap and run
        assert snap.status == "ready" and run.snapshot_id == snap.id
        assert snap.stats["states"] >= 5 and snap.stats["forms"] >= 2
        assert snap.stats["roles"] == {"Admin": "succeeded"}


def test_pages_discovered_through_spa_navigation(crawl: Crawl) -> None:
    titles = {(st.kind, st.title) for st in states(crawl)}
    for page in ("Dashboard", "Items", "Settings", "Admin"):
        assert ("page", page) in titles, titles
    # modules come from the navigation chrome
    modules = {st.module for st in states(crawl) if st.kind == "page"}
    assert {"Dashboard", "Items", "Settings", "Admin"} <= modules


def test_overlays_and_tabs_are_states(crawl: Crawl) -> None:
    sts = states(crawl)
    kinds = {(st.kind, st.title) for st in sts}
    assert ("modal", "Add Item") in kinds
    assert ("modal", "Edit Item") in kinds
    assert any(k == "menu" for k, _ in kinds), "account / row menus recorded"
    assert any(k == "tab" for k, _ in kinds), "switching tabs yields a state"
    modal = next(st for st in sts if st.title == "Add Item")
    assert modal.parent_state_id is not None
    assert modal.nav_path[0] == "Items"


def test_form_fields_with_validation_rules(crawl: Crawl) -> None:
    with Session(engine) as s:
        add = s.exec(
            select(UiState).where(
                UiState.snapshot_id == crawl.snapshot_id, UiState.title == "Add Item"
            )
        ).one()
        form = s.exec(select(UiForm).where(UiForm.state_id == add.id)).one()
        fields = {
            f.label: f
            for f in s.exec(select(FormField).where(FormField.form_id == form.id)).all()
        }
    assert set(fields) >= {"Title", "Description", "Contact Email", "Category"}
    assert form.submit_label == "Save"

    title = fields["Title"]
    assert title.required is True  # from the "Title *" label marker
    msgs = {m["trigger"]: m["message"] for m in title.validation_messages}
    assert msgs["empty"] == "Title is required"
    assert msgs["too_long"] == "Title must be at most 255 characters"

    assert (
        fields["Description"].max_length == 50
    )  # attribute, so no too_long probe needed
    assert not any(
        m["trigger"] == "too_long" for m in fields["Description"].validation_messages
    )
    email = fields["Contact Email"]
    assert any(
        m["trigger"] == "invalid_format" and "valid email" in m["message"]
        for m in email.validation_messages
    )
    assert fields["Category"].options == {
        "values": ["Work", "Home"],
        "truncated": False,
    }


def test_native_html_validation_is_captured(crawl: Crawl) -> None:
    with Session(engine) as s:
        page = s.exec(
            select(UiState).where(
                UiState.snapshot_id == crawl.snapshot_id,
                UiState.title == "Settings",
                UiState.kind == "page",
            )
        ).one()
        form = s.exec(select(UiForm).where(UiForm.state_id == page.id)).one()
        name = s.exec(select(FormField).where(FormField.form_id == form.id)).one()
    assert (name.label, name.required, name.max_length) == ("Display name", True, 20)
    assert any(
        m["trigger"] == "empty" and m["source"] == "native"
        for m in name.validation_messages
    )


def test_nothing_was_mutated_on_the_server(crawl: Crawl) -> None:
    """The headline safety property: only the two role logins reached the server as writes."""
    assert crawl.server.mutations == [], crawl.server.mutations
    assert crawl.server.logins == ["admin@fixture.test"]


def test_destructive_actions_are_blocked_and_logged(crawl: Crawl) -> None:
    with Session(engine) as s:
        blocked = s.exec(
            select(CrawlEvent).where(
                CrawlEvent.run_id == crawl.run_id, CrawlEvent.status == "blocked"
            )
        ).all()
        deleted = s.exec(
            select(UiElement).where(UiElement.is_destructive == True)  # noqa: E712
        ).all()
    labels = {e.element_label for e in blocked}
    assert {"Delete", "Log out"} <= labels, labels
    assert all(
        e.reason and e.reason.startswith("denylist")
        for e in blocked
        if e.element_label in {"Delete", "Log out"}
    )
    # the buttons are still part of the knowledge model, flagged as destructive
    assert {"Delete", "Log out"} <= {e.label for e in deleted}


def test_submit_buttons_never_clicked_and_logged(crawl: Crawl) -> None:
    with Session(engine) as s:
        skipped = s.exec(
            select(CrawlEvent).where(
                CrawlEvent.run_id == crawl.run_id, CrawlEvent.reason == "submit_skipped"
            )
        ).all()
    assert skipped or crawl.server.mutations == []  # either logged, or never attempted
    assert ("POST", "/api/items") not in crawl.server.mutations


def test_crawl_log_explains_skips(crawl: Crawl) -> None:
    with Session(engine) as s:
        rows = s.exec(select(CrawlEvent).where(CrawlEvent.run_id == crawl.run_id)).all()
    reasons = {r.reason for r in rows if r.status == "skipped"}
    assert "external_link" in reasons  # the footer Docs link
    assert "repeated_row_action" in reasons  # second table row is the same component
    assert any(r.status == "crawled" for r in rows)


def test_transitions_form_a_navigable_graph(crawl: Crawl) -> None:
    with Session(engine) as s:
        by_id = {
            st.id: st
            for st in s.exec(
                select(UiState).where(UiState.snapshot_id == crawl.snapshot_id)
            ).all()
        }
        trans = s.exec(
            select(Transition).where(Transition.snapshot_id == crawl.snapshot_id)
        ).all()
    edges = {
        (by_id[t.from_state_id].title, t.via_label, by_id[t.to_state_id].title)
        for t in trans
    }
    assert ("Items", "Add Item", "Add Item") in edges
    assert any(a == "Dashboard" and b == "Items" for a, _, b in edges) or any(
        c == "Items" for _, _, c in edges
    )


def test_role_access_and_screenshots(crawl: Crawl) -> None:
    with Session(engine) as s:
        access = s.exec(
            select(RoleStateAccess).where(
                RoleStateAccess.snapshot_id == crawl.snapshot_id
            )
        ).all()
        sts = list(
            s.exec(
                select(UiState).where(UiState.snapshot_id == crawl.snapshot_id)
            ).all()
        )
    assert len(access) == len(sts) and {a.access for a in access} == {"visible"}
    shots = [st.screenshot_path for st in sts if st.screenshot_path]
    assert len(shots) == len(sts), "every new state has a screenshot"
    assert all((crawl.artifact_dir / p).stat().st_size > 1000 for p in shots)


def test_live_events_were_persisted_for_the_ui(crawl: Crawl) -> None:
    with Session(engine) as s:
        events = s.exec(
            select(AgentEvent)
            .where(AgentEvent.run_id == crawl.run_id)
            .order_by(col(AgentEvent.seq))
        ).all()
    types = [e.type for e in events]
    for t in (
        "state_discovered",
        "form_recorded",
        "transition_recorded",
        "screenshot",
        "tool_call",
        "blocked",
        "skipped",
    ):
        assert t in types, (t, sorted(set(types)))
    assert types[-1] == "run_status" and events[-1].payload["status"] == "succeeded"
    secrets = [pw for pw, _ in USERS.values()]
    assert not any(p in str(e.payload) for e in events for p in secrets), (
        "no password in any event"
    )


def test_icon_font_ligatures_are_not_part_of_labels(crawl: Crawl) -> None:
    """Material-style icons are *text* ("shopping_cart"); they must not leak into names or modules."""
    with Session(engine) as s:
        labels = {
            e.label
            for e in s.exec(
                select(UiElement)
                .join(UiState, col(UiState.id) == col(UiElement.state_id))
                .where(UiState.snapshot_id == crawl.snapshot_id)
            ).all()
        }
        modules = {st.module for st in states(crawl)}
    assert not any(
        w in lbl for lbl in labels for w in ("shopping_cart", "home", "settings_")
    ), labels
    assert {"Dashboard", "Items", "Settings", "Admin"} <= modules
    assert not any(m and "_" in m for m in modules), modules


def test_titles_never_contain_emails_and_fall_back_to_the_route(crawl: Crawl) -> None:
    titles = {(st.kind, st.title) for st in states(crawl)}
    assert ("page", "Profile of <email>") in titles, titles
    assert ("page", "About") in titles, titles  # no heading: derived from /about
    assert not any("@" in st.title for st in states(crawl))
    assert not any("@" in (st.heading or "") for st in states(crawl))


def test_reset_reloads_are_not_recorded_as_transitions(crawl: Crawl) -> None:
    with Session(engine) as s:
        labels = [
            t.via_label
            for t in s.exec(
                select(Transition).where(Transition.snapshot_id == crawl.snapshot_id)
            ).all()
        ]
    assert not any(lbl.startswith("navigate ") for lbl in labels), labels
    assert "View profile" in labels  # a real link click-through path is recorded


def test_static_content_is_captured_and_masked(crawl: Crawl) -> None:
    with Session(engine) as s:
        items = s.exec(
            select(UiState).where(
                UiState.snapshot_id == crawl.snapshot_id,
                UiState.title == "Items",
                UiState.kind == "page",
            )
        ).first()
        dash = s.exec(
            select(UiState).where(
                UiState.snapshot_id == crawl.snapshot_id, UiState.title == "Dashboard"
            )
        ).first()
    assert items and dash
    assert ["Title", "Actions"] in items.content["headers"]
    assert "Welcome back" in dash.content["text"]
    # table body cells are data and never stored
    assert "Alpha" not in str(items.content) and "Beta" not in str(items.content)


def test_hash_routes_are_links_but_page_anchors_are_not() -> None:
    from app.qa.crawler.bfs import _ignored_href

    assert not _ignored_href("#/tickets/new")
    assert not _ignored_href("#!/home")
    assert not _ignored_href("/a/b")
    assert _ignored_href("#top")
    assert _ignored_href("mailto:a@b.c")
    assert _ignored_href("javascript:void(0)")


def test_the_sign_in_screen_is_recorded_untouched(crawl: Crawl) -> None:
    sign_in = [st for st in states(crawl) if st.title == "Sign in"]
    assert len(sign_in) == 1 and sign_in[0].module == "Sign in"
    with Session(engine) as s:
        form = s.exec(select(UiForm).where(UiForm.state_id == sign_in[0].id)).first()
        fields = [
            f.label
            for f in s.exec(select(FormField).where(FormField.form_id == form.id))  # type: ignore[union-attr]
        ]
    assert "Email" in fields and "Password" in fields
    assert crawl.server.logins.count("admin@fixture.test") == 1, (
        "recording it did not sign in twice"
    )
