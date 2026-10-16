"""AGE projection: the graph answers shortest-path questions the same way the relational planner does."""

import uuid

import pytest
from sqlmodel import Session, select

from app.core.db import engine
from app.qa.knowledge.graph import drop_snapshot, project_snapshot, shortest_path
from app.qa.models import Transition, UiState
from tests.qa.conftest import Crawl

pytestmark = pytest.mark.filterwarnings("ignore::ResourceWarning")


def test_projection_counts_and_shortest_path(crawl: Crawl) -> None:
    counts = project_snapshot(crawl.snapshot_id)
    with Session(engine) as s:
        states = list(
            s.exec(select(UiState).where(UiState.snapshot_id == crawl.snapshot_id))
        )
        trans = list(
            s.exec(
                select(Transition).where(Transition.snapshot_id == crawl.snapshot_id)
            )
        )
    assert counts["nodes"] == len(states) and counts["edges"] == len(trans)
    by_title = {st.title: st for st in states if st.kind == "page"}
    dash, add = (
        by_title["Dashboard"],
        next(st for st in states if st.title == "Add Item"),
    )
    path = shortest_path(crawl.snapshot_id, dash.id, add.id)
    assert path is not None and path[0]["screen"] == "Dashboard"
    assert path[-1]["screen"] == "Add Item" and path[-1]["via"] == "Add Item"
    assert len(path) <= 4
    # projecting again replaces rather than duplicates
    assert project_snapshot(crawl.snapshot_id) == counts
    assert shortest_path(crawl.snapshot_id, dash.id, uuid.uuid4()) is None
    drop_snapshot(crawl.snapshot_id)
    assert shortest_path(crawl.snapshot_id, dash.id, add.id) is None
