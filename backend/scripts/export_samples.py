"""Write the sample outputs of every project into docs/samples (knowledge model, test cases, RTM, gap report,
and the knowledge model as JSON), through the public API like a user would.

    uv run python scripts/export_samples.py [--api http://localhost:8000/api/v1] [--out ../docs/samples]
Reads FIRST_SUPERUSER / FIRST_SUPERUSER_PASSWORD from the environment. Test data in the exports uses
placeholders for passwords; no credentials are present."""

import argparse
import os
import re
import sys
from pathlib import Path

import httpx

FILES = [
    ("tests", "xlsx", "tests.xlsx"),
    ("tests", "csv", "tests.csv"),
    ("rtm", "xlsx", "rtm.xlsx"),
    ("gaps", "csv", "gaps.csv"),
    ("km", "xlsx", "km.xlsx"),
    ("km", "json", "km.json"),
]


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000/api/v1")
    ap.add_argument("--out", default="../docs/samples")
    ap.add_argument(
        "--only",
        action="append",
        help="project name to export (repeatable); default: all yours",
    )
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    done: set[str] = set()
    c = httpx.Client(timeout=120)
    tok = c.post(
        f"{a.api}/login/access-token",
        data={
            "username": os.environ["FIRST_SUPERUSER"],
            "password": os.environ["FIRST_SUPERUSER_PASSWORD"],
        },
    ).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    projects = c.get(f"{a.api}/projects/", headers=h).json()
    projects = projects["data"] if isinstance(projects, dict) else projects
    if a.only:
        projects = [p for p in projects if p["name"] in a.only]
    for p in projects:
        summary = c.get(f"{a.api}/projects/{p['id']}/tests/summary", headers=h)
        if summary.status_code != 200 or not summary.json().get("total"):
            continue  # nothing generated yet
        name = slug(p["name"].replace("(demo)", "").replace("(test app)", ""))
        if name in done:
            sys.stderr.write(
                f"skipped '{p['name']}': another project is already named {name}\n"
            )
            continue
        done.add(name)
        for kind, fmt, suffix in FILES:
            r = c.get(
                f"{a.api}/projects/{p['id']}/exports/{kind}",
                headers=h,
                params={"format": fmt},
            )
            if r.status_code == 200:
                (out / f"{name}-{suffix}").write_bytes(r.content)
            else:
                sys.stderr.write(f"{name} {kind}.{fmt}: HTTP {r.status_code}\n")
        sys.stdout.write(f"{name}: written\n")


if __name__ == "__main__":
    main()
