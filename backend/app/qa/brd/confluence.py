"""Import a BRD from a Confluence page (Cloud or Data Center) as Markdown for the ordinary ingest.

The token is used for one request and is never stored, logged or put in an event. The host goes through
the same SSRF guard as the crawl target."""

import re
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
from bs4 import BeautifulSoup, Tag

from app.qa.security.urls import TargetUrlError, validate_target_url

MAX_HTML_BYTES = 5 * 1024 * 1024


class ConfluenceError(Exception):
    """Reported to the user; never contains the token."""


def parse_page_ref(page_url: str) -> tuple[str, str]:
    """-> (site base URL, page id) from a Confluence page URL."""
    try:
        validate_target_url(page_url)
    except TargetUrlError as exc:
        raise ConfluenceError(str(exc)) from exc
    parts = urlsplit(page_url)
    base = f"{parts.scheme}://{parts.netloc}"
    m = re.search(r"/pages/(\d+)", parts.path) or re.search(
        r"/spaces/[^/]+/pages/(\d+)", parts.path
    )
    page_id = m.group(1) if m else (parse_qs(parts.query).get("pageId") or [""])[0]
    if not page_id.isdigit():
        raise ConfluenceError(
            "could not find a page id in that URL (open the page and copy its address, "
            "e.g. https://site.atlassian.net/wiki/spaces/KEY/pages/12345/Title)"
        )
    if parts.path.startswith("/wiki") or parts.netloc.endswith(".atlassian.net"):
        base += "/wiki"
    return base, page_id


def fetch_page(
    base: str,
    page_id: str,
    *,
    email: str | None,
    token: str,
    client: httpx.Client | None = None,
) -> tuple[str, str]:
    """-> (title, HTML body). Cloud uses email + API token (basic); Data Center uses a bearer token."""
    headers = {"Accept": "application/json"}
    auth: httpx.Auth | None = None
    if email:
        auth = httpx.BasicAuth(email, token)
    else:
        headers["Authorization"] = f"Bearer {token}"
    own = client is None
    http = client or httpx.Client(timeout=20, follow_redirects=False)
    try:
        resp = http.get(
            f"{base}/rest/api/content/{page_id}",
            params={"expand": "body.export_view"},
            headers=headers,
            auth=auth,
        )
    except httpx.HTTPError as exc:
        raise ConfluenceError(
            f"could not reach Confluence ({type(exc).__name__})"
        ) from exc
    finally:
        if own:
            http.close()
    if resp.status_code in {401, 403}:
        raise ConfluenceError(
            "Confluence refused the credentials (check the e-mail, token and page permissions)"
        )
    if resp.status_code == 404:
        raise ConfluenceError("page not found (or the account cannot see it)")
    if resp.is_redirect:
        raise ConfluenceError(
            "Confluence redirected the request; use the page's direct URL"
        )
    if resp.status_code != 200:
        raise ConfluenceError(f"Confluence answered HTTP {resp.status_code}")
    try:
        data: dict[str, Any] = resp.json()
        html = data["body"]["export_view"]["value"]
        title = str(data.get("title") or f"Confluence page {page_id}")
    except (ValueError, KeyError, TypeError) as exc:
        raise ConfluenceError("unexpected answer from Confluence") from exc
    if len(html.encode()) > MAX_HTML_BYTES:
        raise ConfluenceError("the page is larger than 5 MB")
    return title, html


def _cell(tag: Tag) -> str:
    return re.sub(r"\s+", " ", tag.get_text(" ", strip=True)).replace("|", "/")


def html_to_markdown(title: str, html: str) -> str:
    """Headings, paragraphs, lists and tables -> Markdown the text parser understands."""
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "svg"]):
        t.decompose()
    out: list[str] = [f"# {title}", ""]

    def walk(node: Tag) -> None:
        for child in node.children:
            if not isinstance(child, Tag):
                continue
            name = child.name
            if re.fullmatch(r"h[1-6]", name):
                out.extend([f"{'#' * min(6, int(name[1]) + 1)} {_cell(child)}", ""])
            elif name in {"ul", "ol"}:
                for i, li in enumerate(child.find_all("li", recursive=False), 1):
                    mark = f"{i}." if name == "ol" else "-"
                    out.append(f"{mark} {_cell(li)}")
                out.append("")
            elif name == "table":
                rows = [
                    [_cell(c) for c in tr.find_all(["th", "td"])]
                    for tr in child.find_all("tr")
                ]
                rows = [r for r in rows if any(r)]
                if rows:
                    width = max(len(r) for r in rows)
                    rows = [r + [""] * (width - len(r)) for r in rows]
                    out.append("| " + " | ".join(rows[0]) + " |")
                    out.append("|" + "---|" * width)
                    out.extend("| " + " | ".join(r) + " |" for r in rows[1:])
                    out.append("")
            elif name in {"p", "pre", "blockquote"}:
                text = _cell(child)
                if text:
                    out.extend([text, ""])
            else:
                walk(child)

    walk(soup)
    return "\n".join(out).strip() + "\n"
