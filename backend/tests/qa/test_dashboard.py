from fastapi.testclient import TestClient

from tests.qa.test_api_projects import API, make_project


def test_dashboard_summarises_projects(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    p = make_project(client, superuser_token_headers)
    try:
        r = client.get(f"{API}/dashboard/", headers=superuser_token_headers)
        assert r.status_code == 200, r.text
        body = r.json()
        mine = next(x for x in body["projects"] if x["id"] == p["id"])
        assert mine["next_step"] == "roles" and mine["requirements"] == 0
        assert body["totals"]["projects"] >= 1
    finally:
        client.delete(f"{API}/projects/{p['id']}", headers=superuser_token_headers)


def test_dashboard_requires_login(client: TestClient) -> None:
    assert client.get(f"{API}/dashboard/").status_code in {401, 403}
