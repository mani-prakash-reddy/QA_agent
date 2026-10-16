"""A tiny SPA served over HTTP: the crawler's deterministic test target.

Behaviours that matter to the crawler: login, client-side routing, a modal form with custom
validation, tabs, a row menu with Delete, a user menu with Log out, an external link, admin-only nav.
Every state-changing request that reaches the server is recorded in `FixtureServer.mutations`."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from app.qa.security.totp import totp

OTP_SEED = "JBSWY3DPEHPK3PXP"
HUMAN_CODE = "424242"
USERS = {
    "totp@fixture.test": ("T0tp-Fixture-pass", "admin"),
    "human@fixture.test": ("Hum4n-Fixture-pass", "admin"),
    "admin@fixture.test": ("Adm1n-Fixture-pass", "admin"),
    "user@fixture.test": ("Us3r-Fixture-pass", "user"),
}

LOGIN_HTML = """<!doctype html><html><head><title>Sign in</title></head><body>
<div role="dialog" aria-label="cookie consent" id="cookies" style="position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:99">
<p style="color:#fff">We use cookies.</p><button type="button" id="ok" onclick="document.getElementById('cookies').remove()">Accept cookies</button></div>
<h1>Sign in</h1>
<input type="text" aria-label="Search the site" placeholder="Search">
<form id="f"><label for="email">Email</label><input id="email" type="email" name="email">
<label for="password">Password</label><input id="password" type="password" name="password">
<button type="button" onclick="document.getElementById('err').textContent='google login not supported'">Sign in with Google</button>
<button type="submit">Sign in</button></form><p id="err" role="alert"></p>__CAPTCHA__
<script>
document.getElementById('f').addEventListener('submit', async (e) => {
  e.preventDefault();
  const r = await fetch('/api/login', {method:'POST', headers:{'content-type':'application/json'},
    body: JSON.stringify({email: email.value, password: password.value})});
  if (!r.ok) { document.getElementById('err').textContent = 'Invalid credentials'; return; }
  const j = await r.json();
  if (j.otp) {
    const em = email.value;
    document.getElementById('f').innerHTML = '<label for="code">Verification code</label>' +
      '<input id="code" name="code" autocomplete="one-time-code"><button type="submit">Verify</button>';
    document.getElementById('f').onsubmit = null;
    document.getElementById('f').addEventListener('submit', async (e2) => {
      e2.preventDefault(); e2.stopImmediatePropagation();
      const v = await fetch('/api/verify', {method:'POST', headers:{'content-type':'application/json'},
        body: JSON.stringify({email: em, code: document.getElementById('code').value})});
      if (v.ok) location.href = '/'; else document.getElementById('err').textContent = 'Wrong code';
    }, true);
    return;
  }
  location.href = '/';
});
</script></body></html>"""

APP_HTML = """<!doctype html><html><head><title>Fixture App</title>
<style>[role=dialog],[role=menu]{position:fixed;background:#fff;border:1px solid #888;padding:12px;z-index:9;top:80px;left:260px}
aside{float:left;width:220px}main{margin-left:240px}</style></head><body>
<aside data-sidebar><nav><a href="/" data-nav><span class="material-icons">home</span> Dashboard</a> <a href="/items" data-nav><span class="material-icons">shopping_cart</span> Items</a>
<a href="/settings" data-nav><span class="material-icons">settings</span> Settings</a> __ADMIN__</nav>
<button id="acct" aria-haspopup="menu">Account</button></aside>
<main id="view"></main>
<footer><a href="https://example.org/docs">Docs</a></footer>
<script>
const ROLE = '__ROLE__';
const rows = ['Alpha', 'Beta'];
const view = document.getElementById('view');
const closeOverlays = () => document.querySelectorAll('[role=dialog],[role=menu]').forEach(n => n.remove());
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeOverlays(); });
function msgFor(input) {
  const v = input.value, rule = input.dataset.rule;
  if (rule === 'title') { if (!v) return 'Title is required'; if (v.length > 255) return 'Title must be at most 255 characters'; }
  if (rule === 'email' && v && !/^\\S+@\\S+\\.\\S+$/.test(v)) return 'Enter a valid email address';
  return '';
}
function bindValidation(root) {
  root.querySelectorAll('[data-rule]').forEach(input => {
    const p = document.createElement('p'); p.id = 'msg-' + input.id; input.after(p);
    input.addEventListener('blur', () => {
      const m = msgFor(input); p.textContent = m;
      if (m) { input.setAttribute('aria-invalid', 'true'); input.setAttribute('aria-describedby', p.id); }
      else { input.removeAttribute('aria-invalid'); input.removeAttribute('aria-describedby'); }
    });
  });
}
function openDialog(title, prefill) {
  closeOverlays();
  const d = document.createElement('div'); d.setAttribute('role', 'dialog'); d.setAttribute('aria-labelledby', 'dlg-title');
  d.innerHTML = `<h2 id="dlg-title">${title}</h2><form>
    <label for="title">Title *</label><input id="title" data-rule="title" value="${prefill || ''}">
    <label for="desc">Description</label><textarea id="desc" maxlength="50"></textarea>
    <label for="mail">Contact Email</label><input id="mail" type="email" data-rule="email">
    <label for="cat">Category</label><select id="cat"><option>Work</option><option>Home</option></select>
    <button type="button" id="cancel">Cancel</button><button type="submit">Save</button></form>`;
  document.body.append(d); bindValidation(d);
  d.querySelector('#cancel').onclick = closeOverlays;
  d.querySelector('form').addEventListener('submit', async e => { e.preventDefault();
    await fetch('/api/items', {method: 'POST', body: '{}'}); closeOverlays(); });
}
function openRowMenu(anchor, name) {
  closeOverlays();
  const m = document.createElement('div'); m.setAttribute('role', 'menu');
  m.innerHTML = `<button role="menuitem" id="edit">Edit</button><button role="menuitem" id="del">Delete</button>`;
  document.body.append(m);
  m.querySelector('#edit').onclick = () => openDialog('Edit Item', name);
  m.querySelector('#del').onclick = async () => { await fetch('/api/items/1', {method: 'DELETE'}); closeOverlays(); };
}
const pages = {
  '/': () => `<h1>Dashboard</h1><p>Welcome back</p><a href="/profile" data-nav>View profile</a> <a href="/about" data-nav>About us</a>`,
  '/profile': () => `<h1>Profile of admin@fixture.test</h1><p>Account details</p>`,
  '/about': () => `<p>No heading on this page</p><button id="x">Contact</button>`,
  '/items': () => `<h1>Items</h1><button id="add">Add Item</button>
    <div role="tablist"><button role="tab" aria-selected="true" id="t1">Active</button><button role="tab" id="t2">Archived</button></div>
    <div id="panel"><table><thead><tr><th>Title</th><th>Actions</th></tr></thead><tbody>
    ${rows.map(r => `<tr><td>${r}</td><td><button class="ra" data-name="${r}" aria-label="Row actions">...</button></td></tr>`).join('')}
    </tbody></table></div>`,
  '/settings': () => `<h1>Settings</h1><form><label for="dn">Display name *</label>
    <input id="dn" required maxlength="20"><button type="submit">Save settings</button></form>`,
  '/admin': () => ROLE === 'admin' ? `<h1>Admin</h1><table><thead><tr><th>User</th></tr></thead><tbody><tr><td>admin</td></tr></tbody></table>`
    : `<h1>Forbidden</h1><p>You do not have access</p>`,
};
function render() {
  closeOverlays();
  view.innerHTML = (pages[location.pathname] || (() => '<h1>Not found</h1>'))();
  view.querySelectorAll('a[data-nav]').forEach(a => a.addEventListener('click', e => {
    e.preventDefault(); history.pushState({}, '', a.getAttribute('href')); render(); }));
  const add = document.getElementById('add'); if (add) add.onclick = () => openDialog('Add Item');
  document.querySelectorAll('.ra').forEach(b => b.onclick = () => openRowMenu(b, b.dataset.name));
  const t2 = document.getElementById('t2'), t1 = document.getElementById('t1');
  if (t2) { t2.onclick = () => { t1.removeAttribute('aria-selected'); t2.setAttribute('aria-selected', 'true');
      document.getElementById('panel').innerHTML = '<h2>Archived items</h2><p>Nothing archived</p>'; };
    t1.onclick = () => render(); }
}
document.querySelectorAll('a[data-nav]').forEach(a => a.addEventListener('click', e => {
  e.preventDefault(); history.pushState({}, '', a.getAttribute('href')); render(); }));
window.addEventListener('popstate', render);
document.getElementById('acct').onclick = () => { closeOverlays();
  const m = document.createElement('div'); m.setAttribute('role', 'menu');
  m.innerHTML = `<a role="menuitem" href="/settings" id="mset">Settings</a><button role="menuitem" id="lo">Log out</button>`;
  document.body.append(m);
  m.querySelector('#mset').addEventListener('click', e => { e.preventDefault(); history.pushState({}, '', '/settings'); render(); });
  m.querySelector('#lo').onclick = async () => { await fetch('/api/logout', {method: 'POST'}); location.href = '/login'; };
};
render();
</script></body></html>"""


class FixtureServer:
    def __init__(self) -> None:
        self.mutations: list[tuple[str, str]] = []
        self.logins: list[str] = []
        self.expire_next = 0  # number of upcoming page loads that answer "signed out"
        self.verified: list[str] = []
        self._lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # silence
                pass

            def _role(self) -> str | None:
                for part in (self.headers.get("Cookie") or "").split(";"):
                    k, _, v = part.strip().partition("=")
                    if k == "role" and v in {"admin", "user"}:
                        return v
                return None

            def _send(
                self,
                code: int,
                body: str,
                ctype: str = "text/html",
                extra: dict[str, str] | None = None,
            ) -> None:
                data = body.encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                if self.path.startswith("/api/"):
                    self._send(
                        200,
                        json.dumps({"items": ["Alpha", "Beta"]}),
                        "application/json",
                    )
                elif self.path.startswith("/login"):
                    captcha = (
                        '<div class="g-recaptcha" data-sitekey="x">I am not a robot</div>'
                        if "captcha" in self.path
                        else ""
                    )
                    self._send(200, LOGIN_HTML.replace("__CAPTCHA__", captcha))
                else:
                    role = self._role()
                    if role and outer.expire_next > 0:
                        outer.expire_next -= 1
                        self._send(
                            302,
                            "",
                            extra={
                                "Location": "/login",
                                "Set-Cookie": "role=; Path=/; Max-Age=0",
                            },
                        )
                        return
                    if not role:
                        self._send(302, "", extra={"Location": "/login"})
                        return
                    admin = (
                        '<a href="/admin" data-nav>Admin</a>' if role == "admin" else ""
                    )
                    self._send(
                        200,
                        APP_HTML.replace("__ROLE__", role).replace("__ADMIN__", admin),
                    )

            def _mutation(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                if self.path == "/api/verify":
                    data = json.loads(body or b"{}")
                    email = data.get("email", "")
                    now = time.time()
                    good = {totp(OTP_SEED, at=now + d) for d in (-30, 0, 30)}
                    expected = HUMAN_CODE if email.startswith("human") else None
                    ok = (
                        data.get("code") == expected
                        if expected
                        else data.get("code") in good
                    )
                    if ok and email in USERS:
                        with outer._lock:
                            outer.verified.append(email)
                        self._send(
                            200,
                            "{}",
                            "application/json",
                            {"Set-Cookie": f"role={USERS[email][1]}; Path=/"},
                        )
                    else:
                        self._send(401, "{}", "application/json")
                    return
                if self.path == "/api/login":
                    data = json.loads(body or b"{}")
                    cred = USERS.get(data.get("email", ""))
                    if cred and cred[0] == data.get("password"):
                        with outer._lock:
                            outer.logins.append(data["email"])
                        if data["email"].startswith(("totp", "human")):
                            self._send(200, '{"otp": true}', "application/json")
                            return
                        self._send(
                            200,
                            "{}",
                            "application/json",
                            {"Set-Cookie": f"role={cred[1]}; Path=/"},
                        )
                    else:
                        self._send(401, "{}", "application/json")
                    return
                with outer._lock:
                    outer.mutations.append((self.command, self.path))
                self._send(200, "{}", "application/json")

            do_POST = do_PUT = do_PATCH = do_DELETE = _mutation  # noqa: N815

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self) -> FixtureServer:
        self._thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
