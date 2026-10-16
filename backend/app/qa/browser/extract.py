"""Turns a live page into a typed Snapshot: elements (with opaque handles), forms, overlay, headings.

Everything the knowledge model knows comes from here, i.e. from what the browser actually rendered."""

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from playwright.async_api import Page

DEFAULT_IGNORE_NAME_RE = (
    r"^open tanstack|^tanstack"  # dev overlays that pollute the tree
)

# Runs in the page. Assigns data-qa-h handles (e1, e2, ...) and returns plain JSON.
EXTRACT_JS = r"""
(opts) => {
  const ignoreRe = new RegExp(opts.ignoreNameRe, 'i');
  const ignoreSel = opts.ignoreSelectors.join(',');
  document.querySelectorAll('[data-qa-h]').forEach(e => e.removeAttribute('data-qa-h'));
  const txt = n => ((n && (n.innerText || n.textContent)) || '').replace(/\s+/g, ' ').trim();
  // Icon fonts render ligature *text* ("arrow_back", "shopping_cart"); it is not part of a control's name.
  const ICON_SEL = 'mat-icon,.mat-icon,.material-icons,.material-icons-outlined,.material-symbols-outlined,' +
    '.material-symbols-rounded,i[class*="fa-"],svg,[aria-hidden="true"]';
  const ownText = n => {
    const c = n.cloneNode(true);
    c.querySelectorAll(ICON_SEL).forEach(x => x.remove());
    return (c.textContent || '').replace(/\s+/g, ' ').trim();
  };
  const visible = el => {
    const s = getComputedStyle(el);
    if (s.visibility === 'hidden' || s.display === 'none') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  // A menu/listbox is a popup only if it floats (fixed/absolute on itself or an ancestor). Some UI kits
  // put role=menu on an in-flow dropdown *trigger* (Fomantic/Semantic UI); treating that as an open
  // overlay would hide the whole page behind it.
  const floats = el => {
    for (let n = el; n && n !== document.body; n = n.parentElement) {
      const p = getComputedStyle(n).position;
      if (p === 'fixed' || p === 'absolute') return true;
    }
    return false;
  };
  const overlays = [...document.querySelectorAll(
    '[role=dialog],[role=alertdialog],[role=menu],[role=listbox],dialog[open]')]
    .filter(visible)
    .filter(el => !['menu', 'listbox'].includes(el.getAttribute('role') || '') || floats(el));
  const overlayEl = overlays.length ? overlays[overlays.length - 1] : null;
  const overlayRole = overlayEl ? (overlayEl.getAttribute('role') || 'dialog') : null;
  const scope = overlayEl || document.body;
  const labelledBy = el => (el.getAttribute('aria-labelledby') || '').split(/\s+/)
    .map(i => txt(document.getElementById(i))).join(' ').trim();

  const accName = el => {
    const al = (el.getAttribute('aria-label') || '').trim();
    if (al) return al;
    const lb = labelledBy(el);
    if (lb) return lb;
    if (el.labels && el.labels.length) {
      const t = [...el.labels].map(txt).join(' ').trim();
      if (t) return t;
    }
    const tag = el.tagName.toLowerCase();
    const type = (el.type || '').toLowerCase();
    if (tag === 'input' && ['submit', 'button', 'reset'].includes(type) && el.value) return el.value.trim();
    if (tag !== 'input' && tag !== 'textarea' && tag !== 'select') {
      const t = ownText(el); if (t) return t;
    }
    return ((el.getAttribute('placeholder') || el.getAttribute('title') || '')).trim();
  };
  const kindOf = el => {
    const tag = el.tagName.toLowerCase(), role = el.getAttribute('role'), type = (el.type || '').toLowerCase();
    if (role === 'tab') return 'tab';
    if (role && role.startsWith('menuitem')) return 'menuitem';
    if (role === 'combobox') return 'combobox';
    if (role === 'switch') return 'switch';
    if (role === 'checkbox' || (tag === 'input' && type === 'checkbox')) return 'checkbox';
    if (role === 'radio' || (tag === 'input' && type === 'radio')) return 'radio';
    if (role === 'option') return 'option';
    if (tag === 'a') return 'link';
    if (tag === 'select') return 'select';
    if (tag === 'textarea') return 'textarea';
    if (tag === 'input') return ['submit', 'button', 'reset', 'image'].includes(type) ? 'button' : 'input';
    return 'button';
  };
  const regionOf = el => {
    if (overlayEl && overlayEl.contains(el)) return 'overlay';
    // Content wrappers can carry sidebar-ish attributes (shadcn: data-slot=sidebar-inset), so the
    // nearest <main> decides first: anything inside it is page content, not app shell.
    if (el.closest('main,[role=main]')) return 'main';
    if (el.closest('nav,aside,[role=navigation],[data-slot^=sidebar],[data-sidebar]')) return 'nav';
    if (el.closest('header,[role=banner]')) return 'header';
    if (el.closest('footer,[role=contentinfo]')) return 'footer';
    return 'main';
  };

  const sel = 'a[href],button,input,select,textarea,[role=tab],[role=menuitem],[role=menuitemcheckbox],' +
    '[role=menuitemradio],[role=combobox],[role=switch],[role=checkbox],[role=radio],[role=option],[role=button]';
  const forms = [];
  const formIndex = new Map();
  const formOf = el => {
    let f = el.closest('form');
    if (!f && overlayEl && overlayRole !== 'menu' && overlayRole !== 'listbox' && overlayEl.contains(el)) f = overlayEl;
    if (!f) return -1;
    if (!formIndex.has(f)) {
      formIndex.set(f, forms.length);
      forms.push({ index: forms.length, name: f === overlayEl ? (labelledBy(f) || f.getAttribute('aria-label') || '') :
        (f.getAttribute('aria-label') || labelledBy(f) || f.getAttribute('name') || ''), fields: [], submit: null });
    }
    return formIndex.get(f);
  };

  const elements = [];
  let n = 0;
  scope.querySelectorAll(sel).forEach(el => {
    if (!visible(el)) return;
    if (ignoreSel && el.closest(ignoreSel)) return;
    const name = accName(el);
    if (ignoreRe.test(name)) return;
    const kind = kindOf(el);
    const handle = 'e' + (++n);
    el.setAttribute('data-qa-h', handle);
    const row = el.closest('tr,[role=row]');
    const inRow = !!(row && row.closest('tbody,[role=rowgroup]'));
    let rowIndex = null;
    if (inRow) rowIndex = [...row.parentElement.children].indexOf(row);
    const describedby = (el.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean)
      .map(i => txt(document.getElementById(i))).filter(Boolean);
    const isFormControl = ['input', 'textarea', 'select', 'combobox', 'checkbox', 'radio', 'switch'].includes(kind);
    const type = (el.type || '').toLowerCase();
    const fi = formOf(el);
    const info = {
      h: handle, kind, role: el.getAttribute('role'), label: name,
      name_attr: el.getAttribute('name'), input_type: kind === 'input' ? (type || 'text') : kind,
      region: regionOf(el), in_row: inRow, row_index: rowIndex, form: fi,
      href: el.tagName.toLowerCase() === 'a' ? el.getAttribute('href') : null,
      disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true',
      required: !!el.required || el.getAttribute('aria-required') === 'true',
      readonly: !!el.readOnly,
      minlength: el.minLength > 0 ? el.minLength : null,
      maxlength: el.maxLength > 0 && el.maxLength < 100000 ? el.maxLength : null,
      min: el.getAttribute('min'), max: el.getAttribute('max'), step: el.getAttribute('step'),
      pattern: el.getAttribute('pattern'), placeholder: el.getAttribute('placeholder'),
      // A <button> without a type attribute reports type=submit even outside a form; it only submits if it has a form.
      is_submit: (el.tagName.toLowerCase() === 'button' || el.tagName.toLowerCase() === 'input')
        && type === 'submit' && !!el.form,
      default_present: isFormControl && kind !== 'checkbox' && kind !== 'radio' && kind !== 'switch'
        ? !!(el.value && String(el.value).length) : false,
      invalid: el.getAttribute('aria-invalid') === 'true',
      expanded: el.getAttribute('aria-expanded'),
      describedby,
      options: kind === 'select' ? [...el.options].map(o => txt(o)).filter(Boolean) : null,
    };
    elements.push(info);
    if (fi >= 0) {
      if (isFormControl) forms[fi].fields.push(handle);
      if (info.is_submit && !forms[fi].submit) forms[fi].submit = handle;
    }
  });

  let rowCount = document.querySelectorAll('tbody tr,[role=row]').length;
  const headings = [...scope.querySelectorAll('h1,h2,h3')].filter(visible).map(txt).filter(Boolean).slice(0, 6);
  // Some apps put the *site name* in an <h1> in the header (Redmine) and the page title in an <h2>.
  const inChrome = n => !!n.closest('header,[role=banner],#header,nav,aside,footer,[role=navigation]');
  const visibleH1 = [...document.querySelectorAll('h1')].filter(visible);
  const bodyH1 = visibleH1.find(n => !inChrome(n));
  const chromeH1 = visibleH1.find(inChrome);
  const contentH2 = [...document.querySelectorAll('h2')].filter(visible).find(n => !inChrome(n));
  let mainH1 = bodyH1 ? txt(bodyH1) : '';
  if (!mainH1 && chromeH1) {
    const site = txt(chromeH1);
    // the header h1 is the site name only if the document title carries it too ("Page - Site")
    mainH1 = contentH2 && document.title.toLowerCase().includes(site.toLowerCase()) ? txt(contentH2) : site;
  }
  const overlayName = overlayEl ? (overlayEl.getAttribute('aria-label') || labelledBy(overlayEl) ||
    txt(overlayEl.querySelector('h1,h2,h3')) || '') : '';
  // Static content a requirement may be about: table column headers and short fixed labels/captions.
  // Table body cells are data and are never read. Masking and filtering happen server-side.
  const headers = [...scope.querySelectorAll('table')].filter(visible).slice(0, 6).map(t =>
    [...t.querySelectorAll('thead th,thead td,th[scope=col],[role=columnheader]')].filter(visible)
      .map(txt).filter(Boolean).slice(0, 14)).filter(h => h.length);
  const textSel = 'p,li,dt,dd,label,legend,caption,figcaption,span,div,small,strong,b,em,h4,h5,h6';
  const BAD = 'tbody,[role=row],script,style,noscript,nav,aside,header,footer,[role=navigation],[data-sidebar],[data-private],[data-sensitive]';
  const texts = [];
  const seenText = new Set();
  for (const el of scope.querySelectorAll(textSel)) {
    if (texts.length >= 60) break;
    if (el.closest(BAD) && !(overlayEl && overlayEl.contains(el))) continue;
    if (el.querySelector(sel) || el.closest(sel)) continue;
    if (![...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())) continue;
    if (!visible(el)) continue;
    const t = ownText(el);
    if (t.length < 3 || t.length > 90 || !/[A-Za-z]/.test(t) || seenText.has(t)) continue;
    seenText.add(t); texts.push(t);
  }
  return {
    url: location.href, title: document.title, heading: mainH1, headings,
    overlay: overlayEl ? { role: overlayRole, name: overlayName } : null,
    elements, forms, row_count: rowCount, headers, texts,
  };
}
"""

_ID_SEGMENT = re.compile(
    r"^(?:\d+|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{16,})$",
    re.I,
)


def route_path(url: str) -> str:
    """The route of a URL. Hash-routed SPAs (`/#/search?q=a`) keep their route in the fragment."""
    parts = urlsplit(url)
    frag = parts.fragment
    if frag.startswith(("/", "!/")):
        return urlsplit(frag.lstrip("!")).path or "/"
    return parts.path or "/"


_PII_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def mask_pii(text: str) -> str:
    """Screen titles are stored and shown to many people: never keep e-mail addresses in them."""
    return _PII_EMAIL.sub("<email>", text)


_NUM = re.compile(r"\d+")
_LONG_TOKEN = re.compile(r"\b[A-Za-z0-9_-]{24,}\b")


def _mask_snippet(text: str) -> str:
    return _NUM.sub("#", _LONG_TOKEN.sub("<token>", mask_pii(text))).strip()


def mask_content(headers: list[list[str]], texts: list[str]) -> dict[str, Any]:
    """Keep what describes a screen (column headers, fixed captions); drop what is probably data.

    A snippet with digits is data ("3 items", "$12.50", an order id) unless it is a label that ends in a
    colon ("Total price:"); e-mail addresses and long tokens are masked; digits that remain become '#'."""
    out_text: list[str] = []
    for t in texts:
        if _NUM.search(t) and not t.rstrip().endswith(":"):
            continue
        masked = _mask_snippet(t)
        if masked and masked not in out_text and not _PII_EMAIL.fullmatch(masked):
            out_text.append(masked[:90])
    out_headers = [
        [_mask_snippet(h)[:40] for h in cols if h.strip()] for cols in headers
    ]
    return {
        "headers": [h for h in out_headers if h][:6],
        "text": out_text[:40],
    }


def route_title(url: str) -> str:
    """Readable fallback title from the route: '/privacy-security/change-password' -> 'Change Password'."""
    last = [s for s in route_path(url).split("/") if s and not _ID_SEGMENT.match(s)]
    return last[-1].replace("-", " ").replace("_", " ").title() if last else ""


def route_template(url: str) -> str:
    """`/items/123?tab=a#x` -> `/items/:id?tab`: ids and values are data, not structure."""
    parts = urlsplit(url)
    frag = parts.fragment
    hash_route = frag.startswith(("/", "!/"))
    query = urlsplit(frag.lstrip("!")).query if hash_route else parts.query
    segments = [
        ":id" if _ID_SEGMENT.match(s) else s for s in route_path(url).split("/")
    ]
    path = "/".join(segments) or "/"
    names = sorted({k for k, _ in parse_qsl(query, keep_blank_values=True)})
    return (
        ("#" if hash_route else "") + path + (("?" + "&".join(names)) if names else "")
    )


def clean_label(label: str) -> tuple[str, bool]:
    """Strip the decorative required marker: 'Title *' -> ('Title', True)."""
    stripped = re.sub(r"\s*\*+\s*$", "", label).strip()
    return stripped, stripped != label.strip()


@dataclass
class Element:
    h: str
    kind: str
    label: str
    key: str = ""
    role: str | None = None
    name_attr: str | None = None
    input_type: str = ""
    region: str = "main"
    in_row: bool = False
    row_index: int | None = None
    form: int = -1
    href: str | None = None
    disabled: bool = False
    required: bool = False
    readonly: bool = False
    minlength: int | None = None
    maxlength: int | None = None
    min: str | None = None
    max: str | None = None
    step: str | None = None
    pattern: str | None = None
    placeholder: str | None = None
    is_submit: bool = False
    default_present: bool = False
    invalid: bool = False
    expanded: str | None = None
    describedby: list[str] = field(default_factory=list)
    options: list[str] | None = None


@dataclass
class FormInfo:
    index: int
    name: str
    fields: list[str]
    submit: str | None


@dataclass
class Snapshot:
    url: str
    title: str
    heading: str
    headings: list[str]
    overlay: dict[str, str] | None
    elements: list[Element]
    forms: list[FormInfo]
    row_count: int
    route: str = ""
    fingerprint: str = ""
    fp_ignore: str = (
        ""  # regex of labels excluded from a screen's identity (shell toggles)
    )
    # Masked static content: {"headers": [[col, ...]], "text": [snippet, ...]}; never table data.
    content: dict[str, Any] = field(default_factory=dict)

    def by_handle(self, handle: str) -> Element | None:
        return next((e for e in self.elements if e.h == handle), None)

    def by_key(self, key: str) -> Element | None:
        return next((e for e in self.elements if e.key == key), None)

    @property
    def overlay_kind(self) -> str | None:
        if not self.overlay:
            return None
        return {"menu": "menu", "listbox": "listbox"}.get(self.overlay["role"], "modal")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _assign_keys(elements: list[Element]) -> None:
    seen: dict[tuple[str, str, str, bool], int] = {}
    for e in elements:
        ident = (e.kind, e.label, e.region, e.in_row)
        nth = seen.get(ident, 0)
        seen[ident] = nth + 1
        # in_row is part of identity: an icon-only row button and an icon-only toolbar button must not collide
        e.key = f"{e.kind}|{e.label}|{e.region}{'/row' if e.in_row else ''}|{nth}"


_EMAIL = re.compile(r"\S+@\S+")
_DIGITS = re.compile(r"\d+")


def _norm_heading(text: str) -> str:
    """Headings often embed data ('Hi, ana@x.com', '3 items'); structure must not depend on it."""
    return _DIGITS.sub("#", _EMAIL.sub("<email>", text)).strip()


def compute_fingerprint(snap: Snapshot, *, include_route: bool = True) -> str:
    """Identity of a *screen*: route + overlay + the structure of its main content.

    App-shell chrome (nav/header/footer) and data rows are excluded: they do not distinguish screens.
    `include_route=False` identifies the same overlay opened from the app shell on any page.
    """
    overlay_sig = (
        f"{snap.overlay['role']}:{snap.overlay['name']}" if snap.overlay else ""
    )
    ignore = re.compile(snap.fp_ignore, re.I) if snap.fp_ignore else None
    structure = sorted(
        {
            (e.kind, e.label)
            for e in snap.elements
            if e.region in {"main", "overlay"}
            and not e.in_row
            and not (ignore and ignore.search(e.label))
        }
    )
    # Sub-headings distinguish tabs/panels on one route. The page's own h1 is excluded: the route
    # identifies the page, and the h1 often carries user data ("Hi, Ana").
    headings = sorted({_norm_heading(h) for h in snap.headings if h != snap.heading})
    raw = repr(
        (
            snap.route if include_route else "",
            overlay_sig,
            headings,
            structure,
        )
    )
    return hashlib.sha256(raw.encode()).hexdigest()


async def extract(
    page: Page,
    *,
    ignore_name_re: str = DEFAULT_IGNORE_NAME_RE,
    ignore_selectors: list[str] | None = None,
    fp_ignore: str = "",
) -> Snapshot:
    raw = await page.evaluate(
        EXTRACT_JS,
        {"ignoreNameRe": ignore_name_re, "ignoreSelectors": ignore_selectors or []},
    )
    elements = [
        Element(**{k: v for k, v in e.items() if k in Element.__dataclass_fields__})
        for e in raw["elements"]
    ]
    for e in elements:
        e.label = re.sub(r"\s+", " ", e.label).strip()[:300]
    _assign_keys(elements)
    snap = Snapshot(
        url=raw["url"],
        title=raw["title"],
        heading=raw["heading"],
        headings=raw["headings"],
        overlay=raw["overlay"],
        elements=elements,
        forms=[
            FormInfo(f["index"], f["name"], f["fields"], f["submit"])
            for f in raw["forms"]
        ],
        row_count=raw["row_count"],
        content=mask_content(raw.get("headers") or [], raw.get("texts") or []),
    )
    snap.route = route_template(snap.url)
    snap.fp_ignore = fp_ignore
    snap.fingerprint = compute_fingerprint(snap)
    return snap
