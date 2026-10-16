"""HelpDesk: support tickets. A hash-routed single-page app (#/tickets ...) with sessions that EXPIRE
(SESSION_TTL seconds, default 300): the next request is answered 401 and the app returns to the sign-in page.

Roles: customer (own tickets), agent (all tickets, can change status/assign), admin (plus SLA and users)."""

import os
from typing import Any

from fastapi import Cookie, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from common import Sessions, users

app = FastAPI(title="HelpDesk")
sessions = Sessions(ttl=int(os.environ.get("SESSION_TTL", "300")))
CATEGORIES = ["Billing", "Technical", "Account", "Other"]
STATUSES = ["Open", "In progress", "Resolved", "Closed"]
SLA = {"Low": 72, "Normal": 24, "High": 8, "Urgent": 2}
TICKETS: list[dict[str, Any]] = [
    {"id": 101, "subject": "Cannot reset my password", "owner": "customer@demo.test", "category": "Account", "priority": "High", "status": "Open", "assignee": "", "comments": [{"by": "customer@demo.test", "text": "The reset email never arrives."}]},
    {"id": 102, "subject": "Invoice shows the wrong VAT", "owner": "customer@demo.test", "category": "Billing", "priority": "Normal", "status": "In progress", "assignee": "agent@demo.test", "comments": []},
    {"id": 103, "subject": "App crashes on export", "owner": "other@demo.test", "category": "Technical", "priority": "Urgent", "status": "Open", "assignee": "", "comments": []},
]
ARTICLES = [
    {"id": 1, "title": "How to reset your password", "body": "Use the Forgot password link on the sign-in page."},
    {"id": 2, "title": "Understanding your invoice", "body": "VAT is shown per line and in total."},
    {"id": 3, "title": "Exporting your data", "body": "Open Settings, then Export."},
]


def me(tok: str | None) -> dict[str, Any]:
    u = sessions.get(tok)
    if not u:
        raise HTTPException(401, "Session expired")
    return u


@app.post("/api/login")
async def login(req: Request) -> Response:
    d = await req.json()
    for u in users():
        if u["email"] == d.get("email") and u["password"] == d.get("password"):
            r = JSONResponse({"name": u["name"], "role": u["role"], "email": u["email"]})
            r.set_cookie("hd", sessions.create(u), httponly=True, samesite="lax")
            return r
    return JSONResponse({"detail": "Wrong email or password"}, 401)


@app.post("/api/logout")
def logout(hd: str | None = Cookie(None)) -> dict[str, bool]:
    sessions.drop(hd)
    return {"ok": True}


@app.get("/api/me")
def whoami(hd: str | None = Cookie(None)) -> dict[str, Any]:
    u = me(hd)
    return {"name": u["name"], "role": u["role"], "email": u["email"]}


@app.get("/api/tickets")
def tickets(hd: str | None = Cookie(None), status: str = "", q: str = "") -> list[dict[str, Any]]:
    u = me(hd)
    return [t for t in TICKETS if (u["role"] != "customer" or t["owner"] == u["email"]) and (not status or t["status"] == status) and q.lower() in t["subject"].lower()]


@app.post("/api/tickets")
async def new_ticket(req: Request, hd: str | None = Cookie(None)) -> Response:
    u = me(hd)
    d = await req.json()
    e: dict[str, str] = {}
    if not d.get("subject", "").strip():
        e["subject"] = "Subject is required"
    elif len(d["subject"]) > 120:
        e["subject"] = "Subject must be at most 120 characters"
    if d.get("category") not in CATEGORIES:
        e["category"] = "Choose a category"
    if d.get("priority") not in SLA:
        e["priority"] = "Choose a priority"
    if len(d.get("description", "").strip()) < 20:
        e["description"] = "Describe the problem in at least 20 characters"
    if e:
        return JSONResponse({"errors": e}, 422)
    t = {"id": max(x["id"] for x in TICKETS) + 1, "subject": d["subject"], "owner": u["email"], "category": d["category"], "priority": d["priority"], "status": "Open", "assignee": "", "comments": [{"by": u["email"], "text": d["description"]}]}
    TICKETS.append(t)
    return JSONResponse({"id": t["id"]})


def ticket_for(u: dict[str, Any], tid: int) -> dict[str, Any]:
    t = next((x for x in TICKETS if x["id"] == tid), None)
    if not t or (u["role"] == "customer" and t["owner"] != u["email"]):
        raise HTTPException(404, "Ticket not found")
    return t


@app.get("/api/tickets/{tid}")
def ticket(tid: int, hd: str | None = Cookie(None)) -> dict[str, Any]:
    return ticket_for(me(hd), tid)


@app.post("/api/tickets/{tid}/comments")
async def comment(tid: int, req: Request, hd: str | None = Cookie(None)) -> Response:
    u = me(hd)
    t = ticket_for(u, tid)
    d = await req.json()
    if not d.get("text", "").strip():
        return JSONResponse({"errors": {"text": "Write a comment first"}}, 422)
    t["comments"].append({"by": u["email"], "text": d["text"][:1000]})
    return JSONResponse({"ok": True})


@app.post("/api/tickets/{tid}/status")
async def set_status(tid: int, req: Request, hd: str | None = Cookie(None)) -> Response:
    u = me(hd)
    if u["role"] == "customer":
        raise HTTPException(403, "Only agents change the status")
    t = ticket_for(u, tid)
    d = await req.json()
    if d.get("status") in STATUSES:
        t["status"] = d["status"]
    if d.get("assign"):
        t["assignee"] = u["email"]
    return JSONResponse({"ok": True})


@app.get("/api/kb")
def kb(hd: str | None = Cookie(None), q: str = "") -> list[dict[str, Any]]:
    me(hd)
    return [a for a in ARTICLES if q.lower() in a["title"].lower()]


@app.get("/api/sla")
def sla(hd: str | None = Cookie(None)) -> dict[str, int]:
    if me(hd)["role"] != "admin":
        raise HTTPException(403, "Admins only")
    return SLA


@app.put("/api/sla")
async def save_sla(req: Request, hd: str | None = Cookie(None)) -> Response:
    if me(hd)["role"] != "admin":
        raise HTTPException(403, "Admins only")
    d = await req.json()
    e: dict[str, str] = {}
    for k in SLA:
        try:
            v = int(d.get(k))
            if not 1 <= v <= 240:
                raise ValueError
            SLA[k] = v
        except (TypeError, ValueError):
            e[k] = f"{k} must be a whole number of hours from 1 to 240"
    return JSONResponse({"errors": e}, 422) if e else JSONResponse(SLA)


@app.get("/api/users")
def list_users(hd: str | None = Cookie(None)) -> list[dict[str, str]]:
    if me(hd)["role"] != "admin":
        raise HTTPException(403, "Admins only")
    return [{"email": u["email"], "name": u["name"], "role": u["role"]} for u in users()]


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>HelpDesk</title>
<style>body{font-family:system-ui,sans-serif;margin:0;background:#f4f6fb;color:#1c2333}
header{background:#1e3a8a;color:#fff;display:flex;align-items:center;gap:20px;padding:0 20px;height:52px}header a{color:#bfdbfe;text-decoration:none;padding:6px 8px;border-radius:6px}
header a.on{background:#1d4ed8;color:#fff}main{max-width:900px;margin:22px auto;padding:0 16px}.card{background:#fff;border:1px solid #dbe1ee;border-radius:10px;padding:16px;margin-bottom:14px}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:8px;border-bottom:1px solid #eef}label{display:block;margin:10px 0 4px;font-weight:600}
input,select,textarea{width:100%;padding:8px;border:1px solid #aab;border-radius:6px;box-sizing:border-box}input[type=radio]{width:auto}
button{padding:8px 14px;border:0;border-radius:6px;background:#1d4ed8;color:#fff;cursor:pointer}button.sec{background:#e5e7eb;color:#111}.err{color:#b91c1c;font-size:13px;min-height:16px}
.chips a{display:inline-block;padding:4px 10px;border:1px solid #99a;border-radius:999px;margin-right:6px;text-decoration:none;color:#223}.chips a.on{background:#1d4ed8;color:#fff}
.pill{padding:2px 8px;border-radius:999px;background:#e0e7ff}</style></head><body><div id="app"></div><script>
const $=(s,r=document)=>r.querySelector(s);let ME=null;
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function api(m,u,b){const r=await fetch(u,{method:m,headers:{'content-type':'application/json'},body:b?JSON.stringify(b):undefined});let j={};try{j=await r.json()}catch(e){}
 if(r.status===401&&u!=='/api/login'){ME=null;location.hash='#/login';render();throw new Error('expired')} return {ok:r.ok,status:r.status,data:j}}
const route=()=>{const h=location.hash.slice(1)||'/tickets';const [p,q]=h.split('?');return {p,q:new URLSearchParams(q||'')}};
const NAV={customer:[['#/tickets','My tickets'],['#/tickets/new','New ticket'],['#/kb','Help articles']],agent:[['#/tickets','All tickets'],['#/kb','Help articles']],admin:[['#/tickets','All tickets'],['#/kb','Help articles'],['#/sla','SLA targets'],['#/users','Users']]};
function shell(body){const {p}=route();return `<header><strong>HelpDesk</strong><nav style="display:flex;gap:4px">${(NAV[ME.role]||[]).map(([h,t])=>`<a href="${h}" class="${('#'+p)===h?'on':''}">${t}</a>`).join('')}</nav><span style="flex:1"></span><span>${esc(ME.name)}</span><button class="sec" id="lo">Sign out</button></header><main>${body}</main>`}
const showErr=e=>{document.querySelectorAll('.err').forEach(x=>x.textContent='');for(const [k,v] of Object.entries(e||{})){const n=$('#e_'+k);if(n)n.textContent=v}};
async function view(){const {p,q}=route();
 if(p==='/tickets/new'){return `<h1>New ticket</h1><form class="card" id="nt"><label for="subject">Subject</label><input id="subject"><div class="err" id="e_subject"></div>
 <label for="category">Category</label><select id="category"><option value="">Choose…</option>${['Billing','Technical','Account','Other'].map(c=>`<option>${c}</option>`).join('')}</select><div class="err" id="e_category"></div>
 <fieldset style="border:0;padding:0"><legend style="font-weight:600;margin-top:10px">Priority</legend>${['Low','Normal','High','Urgent'].map(x=>`<label style="display:inline-block;margin-right:14px;font-weight:400"><input type="radio" name="priority" value="${x}"> ${x}</label>`).join('')}</fieldset><div class="err" id="e_priority"></div>
 <label for="description">Description</label><textarea id="description" rows="5"></textarea><div class="err" id="e_description"></div>
 <label for="attachment">Attachment (optional)</label><input id="attachment" type="file"><p><button type="submit">Submit ticket</button></p></form>`}
 const m=p.match(/^\\/tickets\\/(\\d+)$/);
 if(m){const r=await api('GET','/api/tickets/'+m[1]); if(!r.ok) return '<h1>Not found</h1><div class="card">This ticket does not exist or is not yours.</div>'; const t=r.data;
  const agent=ME.role!=='customer';
  return `<h1>#${t.id} ${esc(t.subject)}</h1><div class="card"><p><span class="pill">${t.status}</span> ${t.priority} priority, ${t.category}. Assigned to: ${esc(t.assignee||'nobody')}</p>
  ${agent?`<label for="status">Status</label><select id="status">${['Open','In progress','Resolved','Closed'].map(s=>`<option${s===t.status?' selected':''}>${s}</option>`).join('')}</select> <button id="setstatus">Update status</button> <button class="sec" id="assign">Assign to me</button>`:''}</div>
  <div class="card"><h2>Conversation</h2>${t.comments.map(c=>`<p><strong>${esc(c.by)}</strong>: ${esc(c.text)}</p>`).join('')}
  <form id="cf"><label for="text">Add a comment</label><textarea id="text" rows="3"></textarea><div class="err" id="e_text"></div><p><button type="submit">Post comment</button></p></form></div>`}
 if(p==='/kb'){const r=await api('GET','/api/kb?q='+encodeURIComponent(q.get('q')||''));
  return `<h1>Help articles</h1><form class="card" id="kf"><label for="kq">Search articles</label><input id="kq" value="${esc(q.get('q')||'')}"><p><button type="submit">Search</button></p></form>${r.data.map(a=>`<div class="card"><h3>${esc(a.title)}</h3><p>${esc(a.body)}</p></div>`).join('')||'<p>No articles found.</p>'}`}
 if(p==='/sla'){const r=await api('GET','/api/sla'); if(!r.ok) return '<h1>Forbidden</h1><div class="card">Admins only.</div>';
  return `<h1>SLA targets</h1><form class="card" id="sf">${Object.entries(r.data).map(([k,v])=>`<label for="sla_${k}">${k} priority: first response (hours)</label><input id="sla_${k}" data-k="${k}" value="${v}"><div class="err" id="e_${k}"></div>`).join('')}<p><button type="submit">Save SLA targets</button></p><p id="ok"></p></form>`}
 if(p==='/users'){const r=await api('GET','/api/users'); if(!r.ok) return '<h1>Forbidden</h1><div class="card">Admins only.</div>';
  return `<h1>Users</h1><div class="card"><table><thead><tr><th>Name</th><th>Email</th><th>Role</th></tr></thead><tbody>${r.data.map(u=>`<tr><td>${esc(u.name)}</td><td>${esc(u.email)}</td><td>${u.role}</td></tr>`).join('')}</tbody></table></div>`}
 const st=q.get('status')||'';const r=await api('GET','/api/tickets?status='+encodeURIComponent(st)+'&q='+encodeURIComponent(q.get('q')||''));
 return `<h1>${ME.role==='customer'?'My tickets':'All tickets'}</h1><div class="chips card">${['','Open','In progress','Resolved','Closed'].map(s=>`<a href="#/tickets?status=${encodeURIComponent(s)}" class="${s===st?'on':''}">${s||'All'}</a>`).join('')}</div>
 <form class="card" id="tf"><label for="tq">Search tickets</label><input id="tq" value="${esc(q.get('q')||'')}"><p><button type="submit">Search</button></p></form>
 <div class="card"><table><thead><tr><th>#</th><th>Subject</th><th>Category</th><th>Priority</th><th>Status</th></tr></thead><tbody>${r.data.map(t=>`<tr><td>${t.id}</td><td><a href="#/tickets/${t.id}">${esc(t.subject)}</a></td><td>${t.category}</td><td>${t.priority}</td><td>${t.status}</td></tr>`).join('')||'<tr><td colspan=5>No tickets</td></tr>'}</tbody></table></div>`}
function bind(){const lo=$('#lo');if(lo)lo.onclick=async()=>{await api('POST','/api/logout');ME=null;location.hash='#/login';render()};
 const nt=$('#nt');if(nt)nt.onsubmit=async e=>{e.preventDefault();const pr=document.querySelector('input[name=priority]:checked');const r=await api('POST','/api/tickets',{subject:$('#subject').value,category:$('#category').value,priority:pr?pr.value:'',description:$('#description').value});if(!r.ok)return showErr(r.data.errors);location.hash='#/tickets/'+r.data.id};
 const cf=$('#cf');if(cf)cf.onsubmit=async e=>{e.preventDefault();const id=route().p.split('/')[2];const r=await api('POST','/api/tickets/'+id+'/comments',{text:$('#text').value});if(!r.ok)return showErr(r.data.errors);render()};
 const ss=$('#setstatus');if(ss)ss.onclick=async()=>{const id=route().p.split('/')[2];await api('POST','/api/tickets/'+id+'/status',{status:$('#status').value});render()};
 const as=$('#assign');if(as)as.onclick=async()=>{const id=route().p.split('/')[2];await api('POST','/api/tickets/'+id+'/status',{assign:true});render()};
 const kf=$('#kf');if(kf)kf.onsubmit=e=>{e.preventDefault();location.hash='#/kb?q='+encodeURIComponent($('#kq').value)};
 const tf=$('#tf');if(tf)tf.onsubmit=e=>{e.preventDefault();location.hash='#/tickets?q='+encodeURIComponent($('#tq').value)};
 const sf=$('#sf');if(sf)sf.onsubmit=async e=>{e.preventDefault();const b={};document.querySelectorAll('[data-k]').forEach(i=>b[i.dataset.k]=i.value);const r=await api('PUT','/api/sla',b);showErr(r.ok?{}:r.data.errors);$('#ok').textContent=r.ok?'SLA targets saved':''}}
async function render(){const app=$('#app');
 if(!ME){try{const r=await fetch('/api/me');if(r.ok)ME=await r.json()}catch(e){}}
 if(!ME){ if(route().p!=='/login')location.hash='#/login';
  app.innerHTML=`<main style="max-width:380px"><h1>Sign in to HelpDesk</h1><form class="card" id="lg"><label for="email">Email</label><input id="email" type="email"><label for="password">Password</label><input id="password" type="password"><div class="err" id="lerr" role="alert"></div><p><button type="submit">Sign in</button></p></form></main>`;
  $('#lg').onsubmit=async e=>{e.preventDefault();const r=await api('POST','/api/login',{email:$('#email').value,password:$('#password').value});if(!r.ok){$('#lerr').textContent=r.data.detail;return}ME=r.data;location.hash='#/tickets'};return}
 if(route().p==='/login'){location.hash='#/tickets';return}
 app.innerHTML=shell(await view());bind()}
addEventListener('hashchange',render);render();
</script></body></html>"""


@app.get("/{path:path}", response_class=HTMLResponse)
def spa(path: str) -> str:
    return PAGE
