"""TestRail connector (API v2): username + API key. Cases go into a chosen section of a project/suite."""

from typing import Any

import httpx

from app.qa.integrations.base import (
    CasePayload,
    Connector,
    IntegrationError,
    Pushed,
    strip_numbering,
)

PRIORITY = {
    "P1": 4,
    "P2": 3,
    "P3": 2,
    "P4": 1,
}  # TestRail defaults: 1 Low .. 4 Critical


class TestRail(Connector):
    __test__ = False
    platform = "testrail"

    def _auth(self) -> httpx.Auth:
        return httpx.BasicAuth(self.username, self._secret)

    def _api(self, endpoint: str) -> str:
        return f"{self.base_url}/index.php?/api/v2/{endpoint}"

    @staticmethod
    def _items(data: Any, key: str) -> list[dict[str, Any]]:
        # TestRail 6.7+ wraps lists as {"offset":..., "<key>": [...]}
        if isinstance(data, dict):
            data = data.get(key, [])
        return [x for x in data if isinstance(x, dict)]

    def test(self) -> str:
        n = len(self._items(self.request("GET", self._api("get_projects")), "projects"))
        return f"connected, {n} project(s) visible"

    def options(self, kind: str, parent: str = "") -> list[dict[str, str]]:
        if kind == "projects":
            rows = self._items(
                self.request("GET", self._api("get_projects")), "projects"
            )
        elif kind == "suites":
            rows = self._items(
                self.request("GET", self._api(f"get_suites/{parent}")), "suites"
            )
        elif kind == "sections":
            project, _, suite = parent.partition(":")
            q = f"&suite_id={suite}" if suite else ""
            rows = self._items(
                self.request("GET", self._api(f"get_sections/{project}{q}")), "sections"
            )
        else:
            raise IntegrationError(f"unknown option list '{kind}'")
        return [{"id": str(r["id"]), "name": str(r.get("name", r["id"]))} for r in rows]

    def destination_key(self, destination: dict[str, Any]) -> str:
        return f"{destination.get('project_id')}/{destination.get('suite_id') or '-'}/{destination.get('section_id')}"

    def _body(self, case: CasePayload, *, separated: bool) -> dict[str, Any]:
        steps = [strip_numbering(s) for s in case.steps] or ["(see title)"]
        body: dict[str, Any] = {
            "title": f"[{case.tc_key}] {case.title}"[:250],
            "priority_id": PRIORITY.get(case.priority, 2),
            "refs": case.requirement[:250],
            "custom_preconds": "\n".join(f"- {p}" for p in case.preconditions),
        }
        if separated:
            body["custom_steps_separated"] = [
                {"content": s, "expected": case.expected if i == len(steps) - 1 else ""}
                for i, s in enumerate(steps)
            ]
        else:
            body["custom_steps"] = "\n".join(
                f"{i}. {s}" for i, s in enumerate(steps, 1)
            )
            body["custom_expected"] = case.expected
        return body

    def _send(self, endpoint: str, case: CasePayload) -> dict[str, Any]:
        try:
            return dict(
                self.request(
                    "POST", self._api(endpoint), json=self._body(case, separated=True)
                )
            )
        except IntegrationError as exc:
            if "custom_steps_separated" not in str(exc):
                raise  # a template without separated steps: send the text form instead
            return dict(
                self.request(
                    "POST", self._api(endpoint), json=self._body(case, separated=False)
                )
            )

    def _pushed(self, data: dict[str, Any]) -> Pushed:
        cid = str(data["id"])
        return Pushed(cid, f"{self.base_url}/index.php?/cases/view/{cid}")

    def create(self, destination: dict[str, Any], case: CasePayload) -> Pushed:
        return self._pushed(self._send(f"add_case/{destination['section_id']}", case))

    def update(
        self, destination: dict[str, Any], external_id: str, case: CasePayload
    ) -> Pushed:
        return self._pushed(self._send(f"update_case/{external_id}", case))
