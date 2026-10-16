"""Drive the whole pipeline for one application through the API: project, roles, BRD, crawl, map, generate.

    uv run python scripts/run_pipeline.py --name "Redmine (demo)" --url http://localhost:3001 \
        --brd ../demo/brds/redmine.md --roles roles.json [--crawl-config '{"max_steps_per_agent":150}'] \
        [--api http://localhost:8000/api/v1] [--skip-crawl]

`roles.json` is a list: [{"name": "Admin", "username": "...", "password": "...", "recipe": {...}}].
It is read here and sent to the API (which encrypts it); keep it out of version control.
Reads FIRST_SUPERUSER / FIRST_SUPERUSER_PASSWORD from the environment."""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx


def say(*parts: object) -> None:
    sys.stdout.write(" ".join(map(str, parts)) + "\n")
    sys.stdout.flush()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--url", required=True)
    ap.add_argument("--brd", required=True)
    ap.add_argument("--roles", required=True)
    ap.add_argument("--api", default="http://localhost:8000/api/v1")
    ap.add_argument("--crawl-config", default="{}")
    ap.add_argument("--skip-crawl", action="store_true")
    ap.add_argument("--skip-map", action="store_true")
    a = ap.parse_args()

    c = httpx.Client(timeout=120)
    tok = c.post(
        f"{a.api}/login/access-token",
        data={
            "username": os.environ["FIRST_SUPERUSER"],
            "password": os.environ["FIRST_SUPERUSER_PASSWORD"],
        },
    ).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}

    def wait(run_id: str) -> dict[str, Any]:
        while True:
            d: dict[str, Any] = c.get(f"{a.api}/runs/{run_id}", headers=h).json()
            if d["status"] in ("succeeded", "failed", "stopped"):
                return d
            time.sleep(3)

    def step(label: str, run_id: str) -> dict[str, Any]:
        t0 = time.time()
        d = wait(run_id)
        say(f"{label}: {d['status']} in {time.time() - t0:.0f}s {d.get('error') or ''}")
        return d

    projects = c.get(f"{a.api}/projects/", headers=h).json()
    projects = projects["data"] if isinstance(projects, dict) else projects
    p = next((x for x in projects if x["name"] == a.name), None)
    if p is None:
        p = c.post(
            f"{a.api}/projects/",
            headers=h,
            json={
                "name": a.name,
                "target_base_url": a.url,
                "environment_ack": True,
                "crawl_config": json.loads(a.crawl_config),
            },
        ).json()
        for r in json.loads(Path(a.roles).read_text()):
            c.post(
                f"{a.api}/projects/{p['id']}/roles",
                headers=h,
                json={
                    "name": r["name"],
                    "login_recipe": r.get("recipe", {}),
                    "credentials": {
                        "username": r["username"],
                        "password": r["password"],
                        **({"totp_seed": r["totp_seed"]} if r.get("totp_seed") else {}),
                    },
                },
            ).raise_for_status()
    pid = p["id"]
    say("project", pid)

    reqs = c.get(
        f"{a.api}/projects/{pid}/requirements", headers=h, params={"limit": 500}
    ).json()["data"]
    if not reqs:
        brd = Path(a.brd)
        r = c.post(
            f"{a.api}/projects/{pid}/brd",
            headers=h,
            files={"file": (brd.name, brd.read_bytes())},
        )
        r.raise_for_status()
        d = step("ingest", r.json()["run_id"])
        reqs = c.get(
            f"{a.api}/projects/{pid}/requirements", headers=h, params={"limit": 500}
        ).json()["data"]
        ids = [x["id"] for x in reqs if not x["needs_decision"]]
        c.post(
            f"{a.api}/projects/{pid}/requirements/bulk",
            headers=h,
            json={"ids": ids, "action": "approve"},
        )
        say(
            f"requirements: {len(reqs)} extracted, {len(ids)} approved; summary {d['summary'].get('requirements')}"
        )
    if not a.skip_crawl:
        r = c.post(
            f"{a.api}/projects/{pid}/runs",
            headers=h,
            json={"kind": "crawl", "params": {}},
        )
        r.raise_for_status()
        d = step("crawl", r.json()["id"])
        say(
            "  stats:",
            {
                k: d["summary"].get("stats", {}).get(k)
                for k in (
                    "states",
                    "forms",
                    "fields",
                    "transitions",
                    "access_probes",
                    "roles",
                )
            },
        )
    if not a.skip_map:
        r = c.post(f"{a.api}/projects/{pid}/mapping/run", headers=h, json={})
        r.raise_for_status()
        d = step("map", r.json()["id"])
        say(
            "  ",
            {
                k: d["summary"].get(k)
                for k in (
                    "matched",
                    "unmatched",
                    "not_ui",
                    "requirements_matched",
                    "feature_gaps",
                )
            },
        )
    r = c.post(f"{a.api}/projects/{pid}/tests/generate", headers=h, json={})
    r.raise_for_status()
    d = step("generate", r.json()["id"])
    say(
        "  ",
        json.dumps({k: d["summary"].get(k) for k in ("cases", "by_type", "dropped")}),
    )
    sys.stdout.write(f"PROJECT_ID={pid}\n")


if __name__ == "__main__":
    main()
