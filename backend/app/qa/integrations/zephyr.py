"""Zephyr Scale (Cloud) connector: an API token from the Jira profile, bearer authentication.
Test cases keep real steps (description / expected result) and live in a chosen folder."""

from typing import Any

from app.qa.integrations.base import (
    CasePayload,
    Connector,
    IntegrationError,
    Pushed,
    label_safe,
    strip_numbering,
)

DEFAULT_BASE = "https://api.zephyrscale.smartbear.com/v2"
PRIORITY = {"P1": "High", "P2": "High", "P3": "Normal", "P4": "Low"}


class Zephyr(Connector):
    platform = "zephyr"

    def __init__(
        self,
        base_url: str,
        username: str,
        secret: str,
        settings: dict[str, Any] | None = None,
        client: Any = None,
    ) -> None:
        super().__init__(base_url or DEFAULT_BASE, username, secret, settings, client)

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._secret}",
        }

    def test(self) -> str:
        data = self.request(
            "GET", f"{self.base_url}/projects", params={"maxResults": 1}
        )
        return f"connected, {data.get('total', len(data.get('values', [])))} project(s) visible"

    def options(self, kind: str, parent: str = "") -> list[dict[str, str]]:
        if kind == "projects":
            data = self.request(
                "GET", f"{self.base_url}/projects", params={"maxResults": 200}
            )
            return [
                {"id": p["key"], "name": f"{p['key']}: {p.get('name', '')}"}
                for p in data.get("values", [])
            ]
        if kind == "folders":
            data = self.request(
                "GET",
                f"{self.base_url}/folders",
                params={
                    "projectKey": parent,
                    "folderType": "TEST_CASE",
                    "maxResults": 200,
                },
            )
            return [
                {"id": str(f["id"]), "name": str(f.get("name", f["id"]))}
                for f in data.get("values", [])
            ]
        raise IntegrationError(f"unknown option list '{kind}'")

    def destination_key(self, destination: dict[str, Any]) -> str:
        return f"{destination.get('project_key')}/{destination.get('folder_id') or '-'}"

    def _steps(self, key: str, case: CasePayload) -> None:
        steps = [strip_numbering(s) for s in case.steps] or ["(see name)"]
        items = [
            {
                "inline": {
                    "description": s,
                    "testData": "",
                    "expectedResult": case.expected if i == len(steps) - 1 else "",
                }
            }
            for i, s in enumerate(steps)
        ]
        self.request(
            "POST",
            f"{self.base_url}/testcases/{key}/teststeps",
            json={"mode": "OVERWRITE", "items": items},
        )

    def _link(self, key: str) -> str:
        site = str(self.settings.get("jira_site", "")).rstrip("/")
        proj = key.split("-")[0]
        return (
            f"{site}/projects/{proj}?selectedItem=com.atlassian.plugins.atlassian-connect-plugin:"
            f"com.kanoah.test-manager__main-project-page#!/testCase/{key}"
            if site
            else ""
        )

    def create(self, destination: dict[str, Any], case: CasePayload) -> Pushed:
        body: dict[str, Any] = {
            "projectKey": destination["project_key"],
            "name": f"[{case.tc_key}] {case.title}"[:255],
            "priorityName": PRIORITY.get(case.priority, "Normal"),
            "objective": f"{case.requirement} ({case.test_type})",
            "precondition": "\n".join(f"- {p}" for p in case.preconditions),
            "labels": [
                x
                for x in (
                    "qa-agent",
                    label_safe(case.test_type),
                    label_safe(case.requirement),
                )
                if x
            ],
        }
        if destination.get("folder_id"):
            body["folderId"] = int(destination["folder_id"])
        data = self.request("POST", f"{self.base_url}/testcases", json=body)
        key = str(data["key"])
        self._steps(key, case)
        return Pushed(key, self._link(key))

    def update(
        self, destination: dict[str, Any], external_id: str, case: CasePayload
    ) -> Pushed:
        current = self.request("GET", f"{self.base_url}/testcases/{external_id}")
        current.update(
            name=f"[{case.tc_key}] {case.title}"[:255],
            objective=f"{case.requirement} ({case.test_type})",
            precondition="\n".join(f"- {p}" for p in case.preconditions),
        )
        self.request("PUT", f"{self.base_url}/testcases/{external_id}", json=current)
        self._steps(external_id, case)
        return Pushed(external_id, self._link(external_id))
