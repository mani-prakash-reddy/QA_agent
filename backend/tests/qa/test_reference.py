"""Reference documents: a business glossary and an API specification, and what they add to mapping,
authoring and the rules of form fields."""

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.qa.generation.rules import form_cases
from app.qa.models import FormField
from app.qa.reference.api_spec import parse_api, related
from app.qa.reference.glossary import ReferenceError, block, expansion, parse_glossary
from app.qa.reference.load import Refs
from tests.qa.test_api_projects import API, make_project
from tests.qa.test_generation import ctx_with

OPENAPI = {
    "openapi": "3.0.0",
    "info": {"title": "Items API", "version": "1.2"},
    "paths": {
        "/items": {
            "post": {
                "summary": "Create an item",
                "tags": ["items"],
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/NewItem"}
                        }
                    }
                },
                "responses": {"201": {}, "422": {}},
            },
            "get": {
                "summary": "List items",
                "parameters": [
                    {"name": "q", "in": "query", "schema": {"type": "string"}}
                ],
                "responses": {"200": {}},
            },
        },
        "/invoices/{id}": {
            "get": {"summary": "Read one invoice", "responses": {"200": {}, "404": {}}}
        },
    },
    "components": {
        "schemas": {
            "NewItem": {
                "type": "object",
                "required": ["title"],
                "properties": {
                    "title": {"type": "string", "maxLength": 40, "minLength": 2},
                    "contactEmail": {"type": "string", "format": "email"},
                    "category": {"type": "string", "enum": ["Work", "Home"]},
                    "quantity": {"type": "integer", "minimum": 1, "maximum": 99},
                },
            }
        }
    },
}


def test_glossary_formats() -> None:
    md = b"# Terms\n\nSKU: stock keeping unit, one per product\n- **Basket** - the shopper's list of items\n\n| Term | Definition | Synonyms |\n|---|---|---|\n| Clerk | warehouse staff | cashier; stock hand |\n"
    terms = {t["term"]: t for t in parse_glossary(md, "g.md")}
    assert set(terms) == {"SKU", "Basket", "Clerk"} and terms["Clerk"]["synonyms"] == [
        "cashier",
        "stock hand",
    ]
    csv = b"term,definition,synonyms\nRMA,return merchandise authorisation,return\n"
    assert parse_glossary(csv, "g.csv")[0]["synonyms"] == ["return"]
    js = json.dumps({"VAT": "value added tax"}).encode()
    assert parse_glossary(js, "g.json")[0]["term"] == "VAT"
    with pytest.raises(ReferenceError):
        parse_glossary(b"nothing useful here", "g.txt")


def test_glossary_only_adds_the_terms_a_text_uses() -> None:
    terms = parse_glossary(
        b"Clerk: warehouse staff | cashier\nSKU: stock keeping unit\n", "g.md"
    )
    terms[0]["synonyms"] = ["cashier"]
    text = "A cashier shall edit a product"
    assert "Clerk: warehouse staff" in block(terms, text) and "SKU" not in block(
        terms, text
    )
    assert block(terms, "nothing relevant") == ""
    assert "Clerk" in expansion(terms, text)


def test_openapi_is_parsed_with_refs_and_swagger2() -> None:
    api = parse_api(json.dumps(OPENAPI).encode(), "a.json")
    ops = {f"{o['method']} {o['path']}": o for o in api["operations"]}
    create = ops["POST /items"]
    title = next(f for f in create["body"] if f["name"] == "title")
    assert title["required"] and title["max_length"] == 40 and title["min_length"] == 2
    assert next(f for f in create["body"] if f["name"] == "category")["enum"] == [
        "Work",
        "Home",
    ]
    assert create["responses"] == ["201", "422"]
    sw = {
        "swagger": "2.0",
        "info": {"title": "S"},
        "paths": {
            "/x": {
                "post": {
                    "parameters": [
                        {
                            "in": "body",
                            "name": "b",
                            "schema": {
                                "properties": {"n": {"type": "string", "maxLength": 5}}
                            },
                        }
                    ],
                    "responses": {"200": {}},
                }
            }
        },
    }
    assert (
        parse_api(json.dumps(sw).encode(), "s.json")["operations"][0]["body"][0][
            "max_length"
        ]
        == 5
    )
    yaml_text = b"openapi: 3.0.0\ninfo: {title: Y}\npaths:\n  /a:\n    get:\n      summary: List a things\n      responses: {'200': {}}\n"
    assert parse_api(yaml_text, "y.yaml")["operations"][0]["path"] == "/a"
    with pytest.raises(ReferenceError):
        parse_api(b'{"hello": 1}', "x.json")


def test_api_rules_fill_in_what_the_screen_does_not_show() -> None:
    refs = Refs(
        operations=parse_api(json.dumps(OPENAPI).encode(), "a.json")["operations"]
    )
    screen = [
        FormField(label="Title"),  # the screen shows no limit
        FormField(label="Contact Email"),
        FormField(label="Quantity", input_type="number"),
        FormField(label="Notes", max_length=7),  # not in the API: untouched
    ]
    out, source = refs.enrich(screen, "The user shall create an item with a title")
    unrelated, none = refs.enrich(screen, "Sign in with an email and a password")
    assert none == {} and unrelated[0].max_length is None, (
        "an unrelated screen gets no API rules"
    )
    by = {f.label: f for f in out}
    assert by["Title"].max_length == 40 and by["Title"].required is True
    assert by["Quantity"].min_value == "1" and by["Quantity"].max_value == "99"
    assert by["Notes"].max_length == 7 and by["Notes"].id not in source
    assert screen[0].max_length is None, "the recorded field is not changed"
    ctx = ctx_with(out)
    ctx.api_source = source
    cases = form_cases(ctx, "FR-1", "Admin", set())
    limit_cases = [
        c for c in cases if "40 characters" in c.title or "41 characters" in c.title
    ]
    assert limit_cases and all(
        c.evidence.get("from_api_spec") == "POST /items" for c in limit_cases
    )


def test_related_operations_by_words() -> None:
    ops = parse_api(json.dumps(OPENAPI).encode(), "a.json")["operations"]
    assert [o["path"] for o in related(ops, "The user shall create an item")][:1] == [
        "/items"
    ]
    assert related(ops, "Pay by credit card") == []
    refs = Refs(operations=ops)
    assert "Backend contract" in refs.api_block("create an item and list items")


def test_reference_endpoints(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    h = superuser_token_headers
    p = make_project(client, h)
    try:
        url = f"{API}/projects/{p['id']}/references"
        bad = client.post(
            url,
            headers=h,
            data={"kind": "api"},
            files={"file": ("a.json", b'{"x": 1}')},
        )
        assert bad.status_code == 422
        ok = client.post(
            url,
            headers=h,
            data={"kind": "api"},
            files={"file": ("a.json", json.dumps(OPENAPI).encode())},
        )
        assert ok.status_code == 200 and "3 operations" in ok.json()["summary"]
        g = client.post(
            url,
            headers=h,
            data={"kind": "glossary"},
            files={"file": ("g.md", b"SKU: stock keeping unit\n")},
        )
        assert g.status_code == 200
        again = client.post(
            url,
            headers=h,
            data={"kind": "glossary"},
            files={"file": ("g.md", b"SKU: stock keeping unit\n")},
        )
        assert again.status_code == 409
        assert {r["kind"] for r in client.get(url, headers=h).json()} == {
            "api",
            "glossary",
        }
        detail = client.get(f"{API}/references/{ok.json()['id']}", headers=h).json()
        assert {o["path"] for o in detail["operations"]} == {"/items", "/invoices/{id}"}
        assert detail["unreferenced_operations"] == 3, (
            "no requirement mentions them yet"
        )
        assert (
            client.delete(f"{API}/references/{g.json()['id']}", headers=h).status_code
            == 200
        )
        assert (
            client.get(f"{API}/references/{uuid.uuid4()}", headers=h).status_code == 404
        )
    finally:
        client.delete(f"{API}/projects/{p['id']}", headers=h)
