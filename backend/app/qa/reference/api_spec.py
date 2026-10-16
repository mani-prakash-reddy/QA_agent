"""API reference (OpenAPI 3.x / Swagger 2, JSON or YAML): the operations and the rules of their inputs. The
rules fill in what the screens do not show (a field's maximum length, an allowed value list), and the
operations tell the author what a requirement's backend contract looks like."""

import json
import re
from typing import Any

import yaml

from app.qa.reference.glossary import ReferenceError

METHODS = ("get", "post", "put", "patch", "delete")
MAX_OPS = 400


def _resolve(spec: dict[str, Any], node: Any, depth: int = 0) -> Any:
    if isinstance(node, dict) and "$ref" in node and depth < 6:
        target: Any = spec
        for part in str(node["$ref"]).lstrip("#/").split("/"):
            target = target.get(part, {}) if isinstance(target, dict) else {}
        return _resolve(spec, target, depth + 1)
    return node


def _schema_fields(
    spec: dict[str, Any], schema: Any, depth: int = 0
) -> list[dict[str, Any]]:
    schema = _resolve(spec, schema)
    if not isinstance(schema, dict) or depth > 2:
        return []
    out: list[dict[str, Any]] = []
    required = set(schema.get("required") or [])
    for name, raw in (schema.get("properties") or {}).items():
        prop = _resolve(spec, raw)
        if not isinstance(prop, dict):
            continue
        out.append(
            {
                "name": name,
                "type": prop.get(
                    "type", "object" if "properties" in prop else "string"
                ),
                "format": prop.get("format"),
                "required": name in required,
                "max_length": prop.get("maxLength"),
                "min_length": prop.get("minLength"),
                "maximum": prop.get("maximum"),
                "minimum": prop.get("minimum"),
                "pattern": prop.get("pattern"),
                "enum": prop.get("enum"),
                "description": str(prop.get("description", ""))[:200],
            }
        )
    for key in ("allOf", "oneOf", "anyOf"):
        for sub in schema.get(key) or []:
            out += _schema_fields(spec, sub, depth + 1)
    return out


def parse_api(data: bytes, filename: str) -> dict[str, Any]:
    text = data.decode("utf-8-sig", errors="replace")
    try:
        spec = (
            json.loads(text) if text.lstrip().startswith("{") else yaml.safe_load(text)
        )
    except (ValueError, yaml.YAMLError) as exc:
        raise ReferenceError(
            "the API specification could not be read (JSON or YAML expected)"
        ) from exc
    if (
        not isinstance(spec, dict)
        or not (spec.get("openapi") or spec.get("swagger"))
        or not isinstance(spec.get("paths"), dict)
    ):
        raise ReferenceError(
            "this is not an OpenAPI / Swagger document (no 'openapi' / 'swagger' and 'paths')"
        )
    ops: list[dict[str, Any]] = []
    for path, item in spec["paths"].items():
        item = _resolve(spec, item)
        if not isinstance(item, dict):
            continue
        shared = item.get("parameters") or []
        for method in METHODS:
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            params = [
                {
                    "name": p.get("name"),
                    "in": p.get("in"),
                    "required": bool(p.get("required")),
                    **{
                        k: (p.get("schema") or p).get(k)
                        for k in (
                            "type",
                            "maxLength",
                            "minimum",
                            "maximum",
                            "enum",
                            "pattern",
                        )
                    },
                }
                for p in (
                    _resolve(spec, x) for x in [*shared, *(op.get("parameters") or [])]
                )
                if isinstance(p, dict)
            ]
            body_schema: Any = None
            rb = _resolve(spec, op.get("requestBody") or {})
            if isinstance(rb, dict):
                content = rb.get("content") or {}
                media: Any = content.get("application/json") or next(
                    iter(content.values()), {}
                )
                body_schema = (media or {}).get("schema")
            for p in op.get("parameters") or []:  # Swagger 2 body parameter
                p = _resolve(spec, p)
                if isinstance(p, dict) and p.get("in") == "body":
                    body_schema = p.get("schema")
            ops.append(
                {
                    "method": method.upper(),
                    "path": path,
                    "summary": str(op.get("summary") or op.get("description") or "")[
                        :200
                    ],
                    "tags": [str(t) for t in op.get("tags") or []],
                    "params": params,
                    "body": _schema_fields(spec, body_schema),
                    "responses": sorted(str(c) for c in (op.get("responses") or {})),
                }
            )
    if not ops:
        raise ReferenceError("the specification lists no operations")
    info = spec.get("info") or {}
    return {
        "title": str(info.get("title", filename)),
        "version": str(info.get("version", "")),
        "operations": ops[:MAX_OPS],
    }


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def field_index(operations: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Request fields by normalised name ("Contact Email", contact_email, contactEmail all meet)."""
    idx: dict[str, dict[str, Any]] = {}
    for op in operations:
        if op["method"] == "GET":
            continue  # a search box is not a data field
        for f in op["body"]:
            idx.setdefault(
                norm(f["name"]), {**f, "operation": f"{op['method']} {op['path']}"}
            )
    return idx


STOP = {
    "the",
    "a",
    "an",
    "of",
    "to",
    "and",
    "or",
    "for",
    "in",
    "on",
    "be",
    "shall",
    "should",
    "can",
    "user",
    "users",
}


def related(
    operations: list[dict[str, Any]], text: str, limit: int = 3
) -> list[dict[str, Any]]:
    """The operations whose path/summary/tags share the most words with `text`."""
    words = {w for w in re.findall(r"[a-z]{3,}", text.lower()) if w not in STOP}
    scored: list[tuple[int, dict[str, Any]]] = []
    for op in operations:
        hay = set(
            re.findall(
                r"[a-z]{3,}",
                f"{op['path']} {op['summary']} {' '.join(op['tags'])}".lower(),
            )
        )
        stems = {w[:5] for w in hay}
        score = sum(1 for w in words if w in hay or w[:5] in stems)
        if score >= 2:
            scored.append((score, op))
    return [o for _s, o in sorted(scored, key=lambda x: -x[0])[:limit]]
