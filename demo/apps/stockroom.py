"""Stockroom: inventory and orders. A classic server-rendered application (full page loads, query-string
filters, pagination, a three-step wizard, tabs by query string, a delete confirmation page, CSV export).

Roles: admin (everything), clerk (products and orders, cannot delete or see suppliers/settings),
viewer (read only: no buttons that change anything). Built as a test target."""

import csv
import io
from html import escape
from typing import Any
from urllib.parse import quote

from fastapi import Cookie, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from common import Sessions, users

app = FastAPI(title="Stockroom")
sessions = Sessions()
CATEGORIES = ["Tools", "Garden", "Office", "Kitchen"]
CURRENCIES = ["EUR", "USD", "GBP"]
SETTINGS = {"low_stock": 10, "currency": "EUR"}
PRODUCTS: list[dict[str, Any]] = [
    {"id": i, "sku": f"SKU-{1000 + i}", "name": n, "category": c, "price": p, "stock": s, "supplier": sup}
    for i, (n, c, p, s, sup) in enumerate(
        [
            ("Cordless drill", "Tools", 89.0, 14, "Northwind"), ("Garden hose 20m", "Garden", 24.5, 3, "GreenCo"),
            ("Desk lamp", "Office", 31.0, 42, "BrightCo"), ("Chef knife", "Kitchen", 55.0, 9, "SharpEdge"),
            ("Hammer", "Tools", 12.0, 80, "Northwind"), ("Pruning shears", "Garden", 18.0, 25, "GreenCo"),
            ("Stapler", "Office", 7.5, 120, "BrightCo"), ("Cutting board", "Kitchen", 19.0, 6, "SharpEdge"),
            ("Spirit level", "Tools", 16.0, 33, "Northwind"), ("Watering can", "Garden", 11.0, 48, "GreenCo"),
            ("Notebook A5", "Office", 3.2, 300, "BrightCo"), ("Frying pan", "Kitchen", 38.0, 17, "SharpEdge"),
        ],
        start=1,
    )
]
ORDERS = [
    {"id": 5001, "customer": "Acme Ltd", "total": 412.0, "status": "New"},
    {"id": 5002, "customer": "Birch & Co", "total": 96.5, "status": "Packed"},
    {"id": 5003, "customer": "Cedar GmbH", "total": 1280.0, "status": "Shipped"},
]
SUPPLIERS = [{"id": 1, "name": "Northwind", "email": "orders@northwind.test"}, {"id": 2, "name": "GreenCo", "email": "hello@greenco.test"}]
WIZARD: dict[str, dict[str, str]] = {}
PER_PAGE = 5


def user_of(tok: str | None) -> dict[str, Any] | None:
    return sessions.get(tok)


def layout(user: dict[str, Any], title: str, body: str, path: str = "") -> HTMLResponse:
    r = user["role"]
    links = [("/", "Dashboard"), ("/products", "Products"), ("/orders", "Orders")]
    if r == "admin":
        links += [("/suppliers", "Suppliers"), ("/settings", "Settings")]
    nav = "".join(f'<a href="{h}"{" class=on" if path == h else ""}>{t}</a>' for h, t in links)
    return HTMLResponse(
        f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{escape(title)} - Stockroom</title>
<style>body{{font-family:system-ui,sans-serif;margin:0;background:#faf7f2;color:#222}}
header{{background:#7c2d12;color:#fff;display:flex;align-items:center;gap:18px;padding:0 20px;height:50px}}
header a{{color:#fed7aa;text-decoration:none;padding:6px 8px;border-radius:6px}}header a.on{{background:#9a3412;color:#fff}}
main{{max-width:960px;margin:22px auto;padding:0 16px}}table{{width:100%;border-collapse:collapse;background:#fff}}
th,td{{text-align:left;padding:8px;border-bottom:1px solid #eee}}.card{{background:#fff;border:1px solid #e5ddd0;border-radius:8px;padding:14px;margin-bottom:14px}}
label{{display:block;margin:10px 0 4px;font-weight:600}}input,select{{padding:7px;border:1px solid #aaa;border-radius:5px;min-width:240px}}
button,.btn{{padding:7px 13px;border:0;border-radius:5px;background:#9a3412;color:#fff;text-decoration:none;cursor:pointer;display:inline-block}}
.btn.sec{{background:#e7e5e4;color:#222}}.err{{color:#b91c1c}}.tabs a{{padding:6px 12px;border:1px solid #ccc;border-radius:6px;margin-right:6px;text-decoration:none;color:#222}}
.tabs a.on{{background:#9a3412;color:#fff}}.pager a{{margin-right:8px}}.low{{color:#b91c1c;font-weight:700}}</style></head><body>
<header><strong>Stockroom</strong><nav style="display:flex;gap:4px">{nav}</nav><span style="flex:1"></span>
<span>{escape(user['name'])} ({r})</span><form method="post" action="/logout" style="margin:0"><button type="submit">Log out</button></form></header>
<main>{body}</main></body></html>"""
    )


def err_list(errors: list[str]) -> str:
    return "".join(f'<p class="err" role="alert">{escape(e)}</p>' for e in errors)


@app.get("/login", response_class=HTMLResponse)
def login_page(error: str = "") -> str:
    msg = f'<p class="err" role="alert">{escape(error)}</p>' if error else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Sign in - Stockroom</title></head>
<body style="font-family:system-ui;max-width:360px;margin:60px auto"><h1>Stockroom</h1><form method="post" action="/login">
<label for="email">Email</label><input id="email" name="email" type="email" required><br>
<label for="password">Password</label><input id="password" name="password" type="password" required><br>{msg}
<p><button type="submit">Sign in</button></p></form></body></html>"""


@app.post("/login")
def login(email: str = Form(""), password: str = Form("")) -> Response:
    for u in users():
        if u["email"] == email and u["password"] == password:
            r = RedirectResponse("/", 303)
            r.set_cookie("sr", sessions.create(u), httponly=True, samesite="lax")
            return r
    return RedirectResponse("/login?error=Invalid+email+or+password", 303)


@app.post("/logout")
def logout(sr: str | None = Cookie(None)) -> Response:
    sessions.drop(sr)
    return RedirectResponse("/login", 303)


def gate(sr: str | None, *roles: str) -> tuple[dict[str, Any] | None, Response | None]:
    u = user_of(sr)
    if not u:
        return None, RedirectResponse("/login", 303)
    if roles and u["role"] not in roles:
        return u, layout(u, "Forbidden", "<h1>Forbidden</h1><p>You do not have access to this page.</p>")
    return u, None


@app.get("/", response_class=HTMLResponse)
def dashboard(sr: str | None = Cookie(None)) -> Response:
    u, deny = gate(sr)
    if deny:
        return deny
    assert u
    low = [p for p in PRODUCTS if p["stock"] <= SETTINGS["low_stock"]]
    return layout(u, "Dashboard", f"""<h1>Dashboard</h1><div class="card"><p><strong>{len(PRODUCTS)}</strong> products,
<strong>{len(low)}</strong> low on stock (at or below {SETTINGS['low_stock']}), <strong>{sum(1 for o in ORDERS if o['status'] == 'New')}</strong> new orders.</p></div>""", "/")


@app.get("/products", response_class=HTMLResponse)
def products(sr: str | None = Cookie(None), q: str = "", category: str = "", page: int = 1) -> Response:
    u, deny = gate(sr)
    if deny:
        return deny
    assert u
    rows = [p for p in PRODUCTS if q.lower() in p["name"].lower() and (not category or p["category"] == category)]
    pages = max(1, -(-len(rows) // PER_PAGE))
    page = min(max(1, page), pages)
    shown = rows[(page - 1) * PER_PAGE : page * PER_PAGE]
    can_edit = u["role"] in {"admin", "clerk"}
    opts = "".join(f'<option{" selected" if c == category else ""}>{c}</option>' for c in CATEGORIES)
    body = [f"""<h1>Products</h1><form method="get" class="card"><label for="q">Search</label><input id="q" name="q" value="{escape(q)}">
<label for="category">Category</label><select id="category" name="category"><option value="">All</option>{opts}</select>
<p><button type="submit">Filter</button> {'<a class="btn" href="/products/new/1">New product</a>' if can_edit else ''}
<a class="btn sec" href="/export/products.csv">Export CSV</a></p></form>
<table><thead><tr><th>SKU</th><th>Name</th><th>Category</th><th>Price</th><th>Stock</th><th>Actions</th></tr></thead><tbody>"""]
    for p in shown:
        low = ' class="low"' if p["stock"] <= SETTINGS["low_stock"] else ""
        acts = f'<a href="/products/{p["id"]}">View</a>'
        if can_edit:
            acts += f' <a href="/products/{p["id"]}/edit">Edit</a>'
        if u["role"] == "admin":
            acts += f' <a href="/products/{p["id"]}/delete">Delete</a>'
        body.append(f'<tr><td>{p["sku"]}</td><td>{escape(p["name"])}</td><td>{p["category"]}</td><td>{p["price"]:.2f} {SETTINGS["currency"]}</td><td{low}>{p["stock"]}</td><td>{acts}</td></tr>')
    body.append("</tbody></table><p class=pager>")
    for n in range(1, pages + 1):
        body.append(f'<a href="/products?q={quote(q)}&category={quote(category)}&page={n}">{"<strong>%d</strong>" % n if n == page else n}</a>')
    body.append(f"</p><p>Page {page} of {pages}</p>")
    return layout(u, "Products", "".join(body), "/products")


def product_form(p: dict[str, str], errors: list[str], action: str, submit: str) -> str:
    opts = "".join(f'<option{" selected" if c == p.get("category") else ""}>{c}</option>' for c in CATEGORIES)
    return f"""{err_list(errors)}<form method="post" action="{action}" class="card">
<label for="name">Product name</label><input id="name" name="name" value="{escape(p.get('name', ''))}">
<label for="sku">SKU</label><input id="sku" name="sku" value="{escape(p.get('sku', ''))}">
<label for="category">Category</label><select id="category" name="category"><option value="">Choose…</option>{opts}</select>
<label for="price">Price</label><input id="price" name="price" value="{escape(p.get('price', ''))}">
<label for="stock">Stock</label><input id="stock" name="stock" value="{escape(p.get('stock', ''))}">
<p><a class="btn sec" href="/products">Cancel</a> <button type="submit">{submit}</button></p></form>"""


def validate(d: dict[str, str], skip_sku: int | None = None) -> list[str]:
    e: list[str] = []
    if not d.get("name", "").strip():
        e.append("Product name is required")
    elif len(d["name"]) > 80:
        e.append("Product name must be at most 80 characters")
    sku = d.get("sku", "").strip()
    if not sku.startswith("SKU-") or not sku[4:].isdigit():
        e.append("SKU must look like SKU-1234")
    elif any(p["sku"] == sku and p["id"] != skip_sku for p in PRODUCTS):
        e.append("This SKU already exists")
    if d.get("category") not in CATEGORIES:
        e.append("Choose a category")
    try:
        if float(d.get("price", "")) <= 0:
            raise ValueError
    except ValueError:
        e.append("Price must be a number greater than 0")
    if not d.get("stock", "").isdigit() or int(d["stock"]) > 100000:
        e.append("Stock must be a whole number from 0 to 100000")
    return e


@app.get("/products/new/{step}", response_class=HTMLResponse)
def wizard(step: int, sr: str | None = Cookie(None)) -> Response:
    u, deny = gate(sr, "admin", "clerk")
    if deny:
        return deny
    assert u
    w = WIZARD.setdefault(u["email"], {})
    if step == 1:
        body = f"""<h1>New product: step 1 of 3, basics</h1><form method="post" action="/products/new/1" class="card">
<label for="name">Product name</label><input id="name" name="name" value="{escape(w.get('name', ''))}">
<label for="sku">SKU</label><input id="sku" name="sku" value="{escape(w.get('sku', ''))}">
<label for="category">Category</label><select id="category" name="category"><option value="">Choose…</option>{''.join(f'<option>{c}</option>' for c in CATEGORIES)}</select>
<p><a class="btn sec" href="/products">Cancel</a> <button type="submit">Next</button></p></form>"""
    elif step == 2:
        body = f"""<h1>New product: step 2 of 3, price and stock</h1><form method="post" action="/products/new/2" class="card">
<label for="price">Price</label><input id="price" name="price" value="{escape(w.get('price', ''))}">
<label for="stock">Opening stock</label><input id="stock" name="stock" value="{escape(w.get('stock', ''))}">
<p><a class="btn sec" href="/products/new/1">Back</a> <button type="submit">Next</button></p></form>"""
    else:
        body = f"""<h1>New product: step 3 of 3, review</h1><div class="card"><p><strong>{escape(w.get('name', ''))}</strong> ({escape(w.get('sku', ''))}),
{w.get('category', '')}, {w.get('price', '')} {SETTINGS['currency']}, {w.get('stock', '')} in stock.</p>
<form method="post" action="/products/new/3"><a class="btn sec" href="/products/new/2">Back</a> <button type="submit">Create product</button></form></div>"""
    return layout(u, "New product", body, "/products")


@app.post("/products/new/{step}")
async def wizard_post(step: int, request: Request, sr: str | None = Cookie(None)) -> Response:
    u, deny = gate(sr, "admin", "clerk")
    if deny:
        return deny
    assert u
    w = WIZARD.setdefault(u["email"], {})
    form = {k: str(v) for k, v in (await request.form()).items()}
    if step == 1:
        w.update({k: form.get(k, "") for k in ("name", "sku", "category")})
        errs = [e for e in validate({**w, "price": "1", "stock": "0"}) if "Price" not in e and "Stock" not in e]
        return layout(u, "New product", f"<h1>Step 1 of 3</h1>{err_list(errs)}<a class='btn' href='/products/new/1'>Back</a>", "/products") if errs else RedirectResponse("/products/new/2", 303)
    if step == 2:
        w.update({k: form.get(k, "") for k in ("price", "stock")})
        errs = [e for e in validate(w) if "Price" in e or "Stock" in e]
        return layout(u, "New product", f"<h1>Step 2 of 3</h1>{err_list(errs)}<a class='btn' href='/products/new/2'>Back</a>", "/products") if errs else RedirectResponse("/products/new/3", 303)
    PRODUCTS.append({"id": len(PRODUCTS) + 1, "sku": w["sku"], "name": w["name"], "category": w["category"], "price": float(w["price"]), "stock": int(w["stock"]), "supplier": ""})
    WIZARD.pop(u["email"], None)
    return RedirectResponse("/products", 303)


def find(pid: int) -> dict[str, Any] | None:
    return next((p for p in PRODUCTS if p["id"] == pid), None)


@app.get("/products/{pid}", response_class=HTMLResponse)
def product(pid: int, sr: str | None = Cookie(None), tab: str = "details") -> Response:
    u, deny = gate(sr)
    if deny:
        return deny
    assert u
    p = find(pid)
    if not p:
        return layout(u, "Not found", "<h1>Not found</h1><p>No such product.</p>")
    tabs = "".join(f'<a href="/products/{pid}?tab={k}" class="{"on" if tab == k else ""}">{t}</a>' for k, t in (("details", "Details"), ("history", "Stock history"), ("suppliers", "Supplier")))
    inner = {
        "details": f"<p>SKU {p['sku']}, category {p['category']}, price {p['price']:.2f} {SETTINGS['currency']}.</p><p>Stock on hand: <strong>{p['stock']}</strong></p>",
        "history": "<table><thead><tr><th>Date</th><th>Change</th><th>Reason</th></tr></thead><tbody><tr><td>2026-09-30</td><td>+20</td><td>Delivery</td></tr><tr><td>2026-10-04</td><td>-6</td><td>Order 5001</td></tr></tbody></table>",
        "suppliers": f"<p>Supplied by <strong>{escape(p['supplier'] or 'nobody yet')}</strong>.</p>",
    }.get(tab, "")
    return layout(u, p["name"], f'<h1>{escape(p["name"])}</h1><div class="tabs">{tabs}</div><div class="card">{inner}</div><p><a href="/products">Back to products</a></p>', "/products")


@app.get("/products/{pid}/edit", response_class=HTMLResponse)
def edit_page(pid: int, sr: str | None = Cookie(None)) -> Response:
    u, deny = gate(sr, "admin", "clerk")
    if deny:
        return deny
    assert u
    p = find(pid)
    if not p:
        return RedirectResponse("/products", 303)
    return layout(u, "Edit product", "<h1>Edit product</h1>" + product_form({k: str(v) for k, v in p.items()}, [], f"/products/{pid}/edit", "Save changes"), "/products")


@app.post("/products/{pid}/edit")
async def edit_post(pid: int, request: Request, sr: str | None = Cookie(None)) -> Response:
    u, deny = gate(sr, "admin", "clerk")
    if deny:
        return deny
    assert u
    p = find(pid)
    form = {k: str(v) for k, v in (await request.form()).items()}
    errs = validate(form, skip_sku=pid)
    if errs or not p:
        return layout(u, "Edit product", "<h1>Edit product</h1>" + product_form(form, errs, f"/products/{pid}/edit", "Save changes"), "/products")
    p.update(name=form["name"], sku=form["sku"], category=form["category"], price=float(form["price"]), stock=int(form["stock"]))
    return RedirectResponse(f"/products/{pid}", 303)


@app.get("/products/{pid}/delete", response_class=HTMLResponse)
def delete_page(pid: int, sr: str | None = Cookie(None)) -> Response:
    u, deny = gate(sr, "admin")
    if deny:
        return deny
    assert u
    p = find(pid)
    name = escape(p["name"]) if p else "this product"
    return layout(u, "Delete product", f'<h1>Delete product</h1><div class="card"><p>Delete <strong>{name}</strong>? This cannot be undone.</p><form method="post" action="/products/{pid}/delete"><a class="btn sec" href="/products">Keep it</a> <button type="submit">Delete product</button></form></div>', "/products")


@app.post("/products/{pid}/delete")
def delete_post(pid: int, sr: str | None = Cookie(None)) -> Response:
    u, deny = gate(sr, "admin")
    if deny:
        return deny
    PRODUCTS[:] = [p for p in PRODUCTS if p["id"] != pid]
    return RedirectResponse("/products", 303)


@app.get("/orders", response_class=HTMLResponse)
def orders(sr: str | None = Cookie(None), status: str = "") -> Response:
    u, deny = gate(sr)
    if deny:
        return deny
    assert u
    chips = "".join(f'<a href="/orders?status={s}" class="{"on" if s == status else ""}">{s or "All"}</a>' for s in ("", "New", "Packed", "Shipped"))
    rows = "".join(f'<tr><td><a href="/orders/{o["id"]}">#{o["id"]}</a></td><td>{escape(o["customer"])}</td><td>{o["total"]:.2f} {SETTINGS["currency"]}</td><td>{o["status"]}</td></tr>' for o in ORDERS if not status or o["status"] == status)
    return layout(u, "Orders", f'<h1>Orders</h1><div class="tabs">{chips}</div><table><thead><tr><th>Order</th><th>Customer</th><th>Total</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table>', "/orders")


@app.get("/orders/{oid}", response_class=HTMLResponse)
def order(oid: int, sr: str | None = Cookie(None)) -> Response:
    u, deny = gate(sr)
    if deny:
        return deny
    assert u
    o = next((x for x in ORDERS if x["id"] == oid), None)
    if not o:
        return layout(u, "Not found", "<h1>Not found</h1>")
    form = ""
    if u["role"] in {"admin", "clerk"}:
        opts = "".join(f'<option{" selected" if s == o["status"] else ""}>{s}</option>' for s in ("New", "Packed", "Shipped"))
        form = f'<form method="post" action="/orders/{oid}/status" class="card"><label for="status">Status</label><select id="status" name="status">{opts}</select> <button type="submit">Update status</button></form>'
    return layout(u, f"Order {oid}", f'<h1>Order #{oid}</h1><div class="card"><p>{escape(o["customer"])}: {o["total"]:.2f} {SETTINGS["currency"]}, status <strong>{o["status"]}</strong></p></div>{form}', "/orders")


@app.post("/orders/{oid}/status")
def order_status(oid: int, status: str = Form(""), sr: str | None = Cookie(None)) -> Response:
    u, deny = gate(sr, "admin", "clerk")
    if deny:
        return deny
    for o in ORDERS:
        if o["id"] == oid and status in {"New", "Packed", "Shipped"}:
            o["status"] = status
    return RedirectResponse(f"/orders/{oid}", 303)


@app.get("/suppliers", response_class=HTMLResponse)
def suppliers(sr: str | None = Cookie(None), error: str = "") -> Response:
    u, deny = gate(sr, "admin")
    if deny:
        return deny
    assert u
    rows = "".join(f"<tr><td>{escape(s['name'])}</td><td>{escape(s['email'])}</td></tr>" for s in SUPPLIERS)
    return layout(u, "Suppliers", f"""<h1>Suppliers</h1>{err_list([error] if error else [])}<table><thead><tr><th>Name</th><th>Email</th></tr></thead><tbody>{rows}</tbody></table>
<form method="post" action="/suppliers" class="card"><h2>Add supplier</h2><label for="name">Supplier name</label><input id="name" name="name"><label for="email">Contact email</label><input id="email" name="email" type="email">
<p><button type="submit">Add supplier</button></p></form>""", "/suppliers")


@app.post("/suppliers")
def add_supplier(name: str = Form(""), email: str = Form(""), sr: str | None = Cookie(None)) -> Response:
    u, deny = gate(sr, "admin")
    if deny:
        return deny
    if not name.strip() or "@" not in email:
        return RedirectResponse("/suppliers?error=Supplier+name+and+a+valid+email+are+required", 303)
    SUPPLIERS.append({"id": len(SUPPLIERS) + 1, "name": name, "email": email})
    return RedirectResponse("/suppliers", 303)


@app.get("/settings", response_class=HTMLResponse)
def settings_page(sr: str | None = Cookie(None), saved: str = "", error: str = "") -> Response:
    u, deny = gate(sr, "admin")
    if deny:
        return deny
    assert u
    opts = "".join(f'<option{" selected" if c == SETTINGS["currency"] else ""}>{c}</option>' for c in CURRENCIES)
    note = "<p>Settings saved.</p>" if saved else ""
    return layout(u, "Settings", f"""<h1>Settings</h1>{note}{err_list([error] if error else [])}<form method="post" action="/settings" class="card">
<label for="low_stock">Low-stock threshold</label><input id="low_stock" name="low_stock" value="{SETTINGS['low_stock']}">
<label for="currency">Currency</label><select id="currency" name="currency">{opts}</select><p><button type="submit">Save settings</button></p></form>""", "/settings")


@app.post("/settings")
def save_settings(low_stock: str = Form(""), currency: str = Form(""), sr: str | None = Cookie(None)) -> Response:
    u, deny = gate(sr, "admin")
    if deny:
        return deny
    if not low_stock.isdigit() or not 0 <= int(low_stock) <= 1000:
        return RedirectResponse("/settings?error=Low-stock+threshold+must+be+a+whole+number+from+0+to+1000", 303)
    SETTINGS.update(low_stock=int(low_stock), currency=currency if currency in CURRENCIES else SETTINGS["currency"])
    return RedirectResponse("/settings?saved=1", 303)


@app.get("/export/products.csv")
def export(sr: str | None = Cookie(None)) -> Response:
    u, deny = gate(sr)
    if deny:
        return deny
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["sku", "name", "category", "price", "stock"])
    for p in PRODUCTS:
        w.writerow([p["sku"], p["name"], p["category"], p["price"], p["stock"]])
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=products.csv"})
