"""Apache AGE projection of a snapshot: screens, how to get between them, who can open what.

Postgres tables stay the source of truth; the graph is derived and can always be rebuilt. It exists for
the questions a graph answers well (shortest click path between two screens, what is reachable from X)."""

import json
import logging
import uuid
from typing import Any

from sqlmodel import Session, select

from app.core.db import engine
from app.qa.models import AppRole, RoleStateAccess, Transition, UiState

logger = logging.getLogger(__name__)
GRAPH = "qa_km"


def _q(value: Any) -> str:
    """A Cypher literal. json.dumps escapes quotes/backslashes; Cypher accepts the same string syntax."""
    return json.dumps(value if value is not None else "")


def _run(cur: Any, cypher: str, columns: int = 1) -> list[tuple[Any, ...]]:
    cols = ", ".join(f"c{i} agtype" for i in range(columns))
    cur.execute(f"SELECT * FROM cypher('{GRAPH}', $q${cypher}$q$) AS ({cols})")  # noqa: S608
    return list(cur.fetchall()) if cur.description else []


def _connect() -> Any:
    conn = engine.raw_connection()
    cur = conn.cursor()
    cur.execute("LOAD 'age'")
    cur.execute('SET search_path = ag_catalog, "$user", public')
    cur.execute("SELECT count(*) FROM ag_graph WHERE name = %s", (GRAPH,))
    row = cur.fetchone()
    if row is None or row[0] == 0:
        cur.execute("SELECT create_graph(%s)", (GRAPH,))
    conn.commit()
    return conn


def project_snapshot(snapshot_id: uuid.UUID) -> dict[str, int]:
    """Replace this snapshot's nodes and edges in the graph. Returns counts."""
    sid = str(snapshot_id)
    with Session(engine) as s:
        states = list(s.exec(select(UiState).where(UiState.snapshot_id == snapshot_id)))
        trans = list(
            s.exec(select(Transition).where(Transition.snapshot_id == snapshot_id))
        )
        access = list(
            s.exec(
                select(RoleStateAccess).where(
                    RoleStateAccess.snapshot_id == snapshot_id
                )
            )
        )
        roles = {r.id: r.name for r in s.exec(select(AppRole))}
    conn = _connect()
    try:
        cur = conn.cursor()
        _run(cur, f"MATCH (n {{sid: {_q(sid)}}}) DETACH DELETE n")
        for st in states:
            _run(
                cur,
                f"CREATE (:Screen {{sid: {_q(sid)}, id: {_q(str(st.id))}, title: {_q(st.title)},"
                f" kind: {_q(st.kind)}, route: {_q(st.route_template)}, module: {_q(st.module)}}})",
            )
        for name in sorted({roles.get(a.role_id, "?") for a in access}):
            _run(cur, f"CREATE (:Role {{sid: {_q(sid)}, name: {_q(name)}}})")
        for t in trans:
            _run(
                cur,
                f"MATCH (a:Screen {{sid: {_q(sid)}, id: {_q(str(t.from_state_id))}}}),"
                f" (b:Screen {{sid: {_q(sid)}, id: {_q(str(t.to_state_id))}}})"
                f" CREATE (a)-[:GOES_TO {{via: {_q(t.via_label)}, kind: {_q(t.kind)}}}]->(b)",
            )
        for a in access:
            _run(
                cur,
                f"MATCH (r:Role {{sid: {_q(sid)}, name: {_q(roles.get(a.role_id, '?'))}}}),"
                f" (b:Screen {{sid: {_q(sid)}, id: {_q(str(a.state_id))}}})"
                f" CREATE (r)-[:ACCESS {{outcome: {_q(a.access)}}}]->(b)",
            )
        conn.commit()
    finally:
        conn.close()
    return {"nodes": len(states), "edges": len(trans), "access": len(access)}


def shortest_path(
    snapshot_id: uuid.UUID, from_id: uuid.UUID, to_id: uuid.UUID, max_hops: int = 10
) -> list[dict[str, str]] | None:
    """Fewest-click path between two screens as [{screen, via}], or None when unreachable."""
    sid = str(snapshot_id)
    conn = _connect()
    try:
        cur = conn.cursor()
        for hops in range(1, max_hops + 1):  # iterative deepening = shortest first
            rows = _run(
                cur,
                f"MATCH p = (a:Screen {{sid: {_q(sid)}, id: {_q(str(from_id))}}})"
                f"-[:GOES_TO*{hops}..{hops}]->(b:Screen {{sid: {_q(sid)}, id: {_q(str(to_id))}}})"
                " RETURN [n IN nodes(p) | n.title], [r IN relationships(p) | r.via] LIMIT 1",
                columns=2,
            )
            if rows:
                titles = json.loads(str(rows[0][0]))
                vias = json.loads(str(rows[0][1]))
                return [
                    {"screen": titles[0], "via": ""},
                    *[
                        {"screen": titles[i + 1], "via": vias[i]}
                        for i in range(len(vias))
                    ],
                ]
        return None
    finally:
        conn.close()


def drop_snapshot(snapshot_id: uuid.UUID) -> None:
    conn = _connect()
    try:
        _run(
            conn.cursor(), f"MATCH (n {{sid: {_q(str(snapshot_id))}}}) DETACH DELETE n"
        )
        conn.commit()
    finally:
        conn.close()
