"""Validation probing: learn a form's rules without submitting it.

We trigger the app's own client-side validation (blur / input events) and read the messages it shows.
Nothing is submitted, and the safe-mode network guard would abort a submit anyway."""

from dataclasses import dataclass
from typing import Any

from app.qa.browser.extract import Element
from app.qa.browser.session import RoleBrowser

READ_JS = """(h) => {
  const el = document.querySelector(`[data-qa-h="${h}"]`);
  if (!el) return null;
  const txt = n => ((n && (n.innerText || n.textContent)) || '').replace(/\\s+/g, ' ').trim();
  const ids = ((el.getAttribute('aria-describedby') || '') + ' ' + (el.getAttribute('aria-errormessage') || ''))
    .split(/\\s+/).filter(Boolean);
  return {
    invalid: el.getAttribute('aria-invalid') === 'true',
    texts: ids.map(i => txt(document.getElementById(i))).filter(Boolean),
    native: el.validity && !el.validity.valid ? (el.validationMessage || '') : '',
  };
}"""

TEXT_TYPES = {"text", "email", "url", "tel", "search", "password", ""}
LONG_VALUE = "x" * 300


def _long_value(input_type: str) -> str:
    """A value that is valid for its type but far too long."""
    if input_type == "email":
        return "a" * 300 + "@example.com"
    if input_type == "url":
        return "https://example.com/" + "a" * 300
    return LONG_VALUE


@dataclass
class ProbeResult:
    handle: str
    trigger: str  # empty | too_long | invalid_format
    message: str
    source: str  # app | native


async def _read(rb: RoleBrowser, handle: str) -> dict[str, Any]:
    result = await rb.page.evaluate(READ_JS, handle)
    return result or {"invalid": False, "texts": [], "native": ""}


async def _try_value(
    rb: RoleBrowser, el: Element, value: str, trigger: str, baseline: list[str]
) -> ProbeResult | None:
    await rb.fill(el.h, value)
    await rb.blur_active()
    await rb.page.wait_for_timeout(250)
    after = await _read(rb, el.h)
    new = [t for t in after["texts"] if t not in baseline]
    if new:
        return ProbeResult(el.h, trigger, new[0], "app")
    if after["invalid"] and after["texts"]:
        return ProbeResult(el.h, trigger, after["texts"][0], "app")
    if after["native"]:
        return ProbeResult(el.h, trigger, after["native"], "native")
    return None


async def probe_field(rb: RoleBrowser, el: Element) -> list[ProbeResult]:
    if el.kind not in {"input", "textarea"} or el.readonly or el.disabled:
        return []
    if el.input_type not in TEXT_TYPES and el.kind != "textarea":
        return []
    results: list[ProbeResult] = []
    baseline = (await _read(rb, el.h))["texts"]
    try:
        # empty: required-ness as the app expresses it
        hit = await _try_value(rb, el, "", "empty", baseline)
        if hit:
            results.append(hit)
        # wrong format first, so a format error is never mistaken for a length error
        bad = {"email": "not-an-email", "url": "not a url"}.get(el.input_type)
        if bad is None and el.pattern:
            bad = "!!!"
        if bad:
            hit = await _try_value(rb, el, bad, "invalid_format", baseline)
            if hit and all(hit.message != r.message for r in results):
                results.append(hit)
        # too long: only when the field has no maxlength attribute (otherwise the browser truncates).
        # The long value must itself be well-formed, or we would only re-trigger the format error.
        if el.maxlength is None and el.input_type != "password" and not el.pattern:
            hit = await _try_value(
                rb, el, _long_value(el.input_type), "too_long", baseline
            )
            if hit and all(hit.message != r.message for r in results):
                results.append(hit)
    finally:
        try:
            await rb.fill(el.h, "")
            await rb.blur_active()
        except Exception:
            pass
    return results
