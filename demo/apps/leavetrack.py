"""LeaveTrack: leave management. A single-page app (history-API routes) over a small JSON API.

Roles: employee, manager, hr. HR can be given a TOTP seed (second factor at sign-in).
Built as a test target: rules with exact limits, role-specific screens, a destructive-looking action."""

from datetime import date
from typing import Any

from fastapi import Cookie, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from common import Sessions, totp_ok, users

app = FastAPI(title="LeaveTrack")
sessions = Sessions()
LEAVE_TYPES = ["Annual", "Sick", "Unpaid", "Parental"]
DEPARTMENTS = ["Engineering", "Sales", "Finance", "Support"]
POLICY = {"annual_days": 24, "carry_over_max": 5, "min_notice_days": 2}
EMPLOYEES: list[dict[str, Any]] = [
    {"id": 1, "name": "Uma User", "email": "user@demo.test", "department": "Engineering", "role": "employee", "start": "2023-02-01"},
    {"id": 2, "name": "Mia Manager", "email": "manager@demo.test", "department": "Engineering", "role": "manager", "start": "2021-06-14"},
]
REQUESTS: list[dict[str, Any]] = [
    {"id": 1, "who": "Uma User", "type": "Annual", "from": "2026-11-02", "to": "2026-11-06", "days": 5, "status": "Pending", "reason": "Family trip"},
    {"id": 2, "who": "Uma User", "type": "Sick", "from": "2026-09-10", "to": "2026-09-11", "days": 2, "status": "Approved", "reason": "Flu"},
]


def me(token: str | None) -> dict[str, Any]:
    user = sessions.get(token)
    if not user:
        raise HTTPException(401, "Not signed in")
    return user


def need(user: dict[str, Any], *roles: str) -> None:
    if user["role"] not in roles:
        raise HTTPException(403, "You do not have access to this")


@app.post("/api/login")
async def login(req: Request) -> Response:
    data = await req.json()
    for u in users():
        if u["email"] == data.get("email") and u["password"] == data.get("password"):
            if u.get("totp_seed"):
                if not data.get("code"):
                    return JSONResponse({"need_code": True})
                if not totp_ok(u["totp_seed"], str(data["code"])):
                    return JSONResponse({"detail": "Invalid verification code"}, 401)
            token = sessions.create(u)
            r = JSONResponse({"name": u["name"], "role": u["role"]})
            r.set_cookie("lt", token, httponly=True, samesite="lax")
            return r
    return JSONResponse({"detail": "Invalid email or password"}, 401)


@app.post("/api/logout")
def logout(lt: str | None = Cookie(None)) -> dict[str, bool]:
    sessions.drop(lt)
    return {"ok": True}


@app.get("/api/me")
def whoami(lt: str | None = Cookie(None)) -> dict[str, Any]:
    u = me(lt)
    return {"name": u["name"], "role": u["role"], "email": u["email"]}


@app.get("/api/leave")
def my_leave(lt: str | None = Cookie(None)) -> dict[str, Any]:
    u = me(lt)
    mine = [r for r in REQUESTS if r["who"] == u["name"]]
    used = sum(r["days"] for r in mine if r["status"] in {"Approved", "Pending"} and r["type"] == "Annual")
    return {"requests": mine, "balance": POLICY["annual_days"] - used, "entitlement": POLICY["annual_days"]}


@app.post("/api/leave")
async def request_leave(req: Request, lt: str | None = Cookie(None)) -> Response:
    u = me(lt)
    d = await req.json()
    errors: dict[str, str] = {}
    if d.get("type") not in LEAVE_TYPES:
        errors["type"] = "Choose a leave type"
    try:
        a, b = date.fromisoformat(d.get("from", "")), date.fromisoformat(d.get("to", ""))
    except ValueError:
        errors["from"] = "Enter valid dates"
        a = b = date.today()
    if not errors and b < a:
        errors["to"] = "End date cannot be before the start date"
    if len(d.get("reason", "")) > 200:
        errors["reason"] = "Reason must be at most 200 characters"
    days = (b - a).days + 1
    if not errors and d["type"] == "Annual":
        bal = my_leave(lt)["balance"]
        if days > bal:
            errors["to"] = f"Not enough balance: {bal} day(s) left"
    if errors:
        return JSONResponse({"errors": errors}, 422)
    REQUESTS.append({"id": len(REQUESTS) + 1, "who": u["name"], "type": d["type"], "from": str(a), "to": str(b), "days": days, "status": "Pending", "reason": d.get("reason", "")})
    return JSONResponse({"ok": True})


@app.get("/api/team")
def team(lt: str | None = Cookie(None)) -> list[dict[str, Any]]:
    need(me(lt), "manager", "hr")
    return [r for r in REQUESTS if r["status"] == "Pending"]


@app.post("/api/team/{rid}/decision")
async def decide(rid: int, req: Request, lt: str | None = Cookie(None)) -> Response:
    need(me(lt), "manager", "hr")
    d = await req.json()
    if d.get("decision") == "Rejected" and len(d.get("comment", "").strip()) < 5:
        return JSONResponse({"errors": {"comment": "A comment of at least 5 characters is required to reject"}}, 422)
    for r in REQUESTS:
        if r["id"] == rid:
            r["status"] = d["decision"]
    return JSONResponse({"ok": True})


@app.get("/api/employees")
def employees(lt: str | None = Cookie(None)) -> list[dict[str, Any]]:
    need(me(lt), "hr")
    return EMPLOYEES


@app.post("/api/employees")
async def add_employee(req: Request, lt: str | None = Cookie(None)) -> Response:
    need(me(lt), "hr")
    d = await req.json()
    errors: dict[str, str] = {}
    if not d.get("name", "").strip():
        errors["name"] = "Name is required"
    elif len(d["name"]) > 60:
        errors["name"] = "Name must be at most 60 characters"
    if "@" not in d.get("email", "") or "." not in d.get("email", "").split("@")[-1]:
        errors["email"] = "Enter a valid email address"
    elif any(e["email"] == d["email"] for e in EMPLOYEES):
        errors["email"] = "This email is already registered"
    if d.get("department") not in DEPARTMENTS:
        errors["department"] = "Choose a department"
    if errors:
        return JSONResponse({"errors": errors}, 422)
    EMPLOYEES.append({"id": len(EMPLOYEES) + 1, **{k: d.get(k, "") for k in ("name", "email", "department", "role", "start")}})
    return JSONResponse({"ok": True})


@app.get("/api/policies")
def policies(lt: str | None = Cookie(None)) -> dict[str, int]:
    need(me(lt), "hr")
    return POLICY


@app.put("/api/policies")
async def save_policies(req: Request, lt: str | None = Cookie(None)) -> Response:
    need(me(lt), "hr")
    d = await req.json()
    errors: dict[str, str] = {}
    for key, lo, hi, label in (("annual_days", 0, 60, "Annual days"), ("carry_over_max", 0, 10, "Carry-over"), ("min_notice_days", 0, 30, "Notice")):
        try:
            v = int(d.get(key))
            if not lo <= v <= hi:
                raise ValueError
            POLICY[key] = v
        except (TypeError, ValueError):
            errors[key] = f"{label} must be a whole number from {lo} to {hi}"
    return JSONResponse({"errors": errors}, 422) if errors else JSONResponse(POLICY)


@app.get("/api/reports")
def reports(lt: str | None = Cookie(None)) -> dict[str, Any]:
    need(me(lt), "hr")
    by_type: dict[str, int] = {}
    for r in REQUESTS:
        by_type[r["type"]] = by_type.get(r["type"], 0) + r["days"]
    return {"by_type": by_type, "by_department": {"Engineering": len(REQUESTS), "Sales": 0, "Finance": 0, "Support": 0}}


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>LeaveTrack</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{font-family:system-ui,sans-serif;margin:0;background:#f6f7f9;color:#1b1f24}
header{background:#14532d;color:#fff;display:flex;align-items:center;gap:24px;padding:0 20px;height:52px}
header a{color:#d1fae5;text-decoration:none;padding:6px 8px;border-radius:6px}header a.on{background:#166534;color:#fff}
header .sp{flex:1}main{max-width:980px;margin:24px auto;padding:0 16px}
.card{background:#fff;border:1px solid #dde1e6;border-radius:10px;padding:16px;margin-bottom:16px}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:8px;border-bottom:1px solid #eee}
label{display:block;margin:10px 0 4px;font-weight:600}input,select,textarea{width:100%;padding:8px;border:1px solid #bbb;border-radius:6px;box-sizing:border-box}
button{padding:8px 14px;border:0;border-radius:6px;background:#15803d;color:#fff;cursor:pointer}button.sec{background:#e5e7eb;color:#111}
button.danger{background:#b91c1c}.err{color:#b91c1c;font-size:13px;min-height:16px}
.modal{position:fixed;inset:0;background:rgba(0,0,0,.45);display:flex;align-items:center;justify-content:center}
.modal>div{background:#fff;border-radius:10px;padding:20px;width:420px;max-width:92vw}
.menu{position:absolute;right:20px;top:50px;background:#fff;color:#111;border:1px solid #ccc;border-radius:8px;padding:6px;min-width:160px;z-index:5}
.menu button{display:block;width:100%;text-align:left;background:none;color:#111}
.stat{display:inline-block;margin-right:28px}.stat b{font-size:28px;display:block}
.tabs button{background:#e5e7eb;color:#111;margin-right:6px}.tabs button.on{background:#15803d;color:#fff}
</style></head><body><div id="app"></div><script>
const $=(s,r=document)=>r.querySelector(s);
let ME=null, MENU=false, MODAL=null;
const api=async(m,u,b)=>{const r=await fetch(u,{method:m,headers:{'content-type':'application/json'},body:b?JSON.stringify(b):undefined});
  let j={};try{j=await r.json()}catch(e){} if(r.status===401&&u!=='/api/login'){ME=null;go('/login');throw new Error('401')} return {ok:r.ok,status:r.status,data:j}};
const go=(p)=>{history.pushState({},'',p);render()};
const NAV={employee:[['/','Dashboard'],['/leave','My leave']],manager:[['/','Dashboard'],['/leave','My leave'],['/team','Team requests']],
 hr:[['/','Dashboard'],['/leave','My leave'],['/team','Team requests'],['/employees','Employees'],['/policies','Leave policies'],['/reports','Reports']]};
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function shell(body){const nav=(NAV[ME.role]||[]).map(([p,t])=>`<a href="${p}" data-nav class="${location.pathname===p?'on':''}">${t}</a>`).join('');
 return `<header><strong>LeaveTrack</strong><nav style="display:flex;gap:4px">${nav}</nav><span class="sp"></span>
 <button class="sec" id="acct" aria-haspopup="menu">${esc(ME.name)}</button></header>${MENU?`<div class="menu" role="menu"><button role="menuitem" data-go="/profile">Profile</button><button role="menuitem" id="lo">Log out</button></div>`:''}
 <main>${body}</main>${MODAL?MODAL:''}`}
async function page(){const p=location.pathname;
 if(p==='/leave'){const {data}=await api('GET','/api/leave');
  return `<h1>My leave</h1><div class="card"><span class="stat"><b>${data.balance}</b>days left</span><span class="stat"><b>${data.entitlement}</b>days a year</span>
  <button id="new">Request leave</button></div><div class="card"><table><thead><tr><th>Type</th><th>From</th><th>To</th><th>Days</th><th>Status</th></tr></thead><tbody>${data.requests.map(r=>`<tr><td>${r.type}</td><td>${r.from}</td><td>${r.to}</td><td>${r.days}</td><td>${r.status}</td></tr>`).join('')}</tbody></table></div>`}
 if(p==='/team'){const r=await api('GET','/api/team'); if(!r.ok) return forbidden();
  return `<h1>Team requests</h1><div class="card"><table><thead><tr><th>Employee</th><th>Type</th><th>Dates</th><th>Days</th><th>Decision</th></tr></thead><tbody>${r.data.map(x=>`<tr><td>${x.who}</td><td>${x.type}</td><td>${x.from} to ${x.to}</td><td>${x.days}</td><td><button data-approve="${x.id}">Approve</button> <button class="danger" data-reject="${x.id}">Reject</button></td></tr>`).join('')||'<tr><td colspan=5>No pending requests</td></tr>'}</tbody></table></div>`}
 if(p==='/employees'){const r=await api('GET','/api/employees'); if(!r.ok) return forbidden();
  return `<h1>Employees</h1><div class="card"><button id="addemp">Add employee</button></div><div class="card"><table><thead><tr><th>Name</th><th>Email</th><th>Department</th><th>Role</th></tr></thead><tbody>${r.data.map(e=>`<tr><td>${esc(e.name)}</td><td>${esc(e.email)}</td><td>${e.department}</td><td>${e.role}</td></tr>`).join('')}</tbody></table></div>`}
 if(p==='/policies'){const r=await api('GET','/api/policies'); if(!r.ok) return forbidden();
  return `<h1>Leave policies</h1><form class="card" id="pol"><label for="annual_days">Annual leave days</label><input id="annual_days" type="number" value="${r.data.annual_days}"><div class="err" id="e_annual_days"></div>
  <label for="carry_over_max">Maximum carry-over days</label><input id="carry_over_max" type="number" value="${r.data.carry_over_max}"><div class="err" id="e_carry_over_max"></div>
  <label for="min_notice_days">Minimum notice (days)</label><input id="min_notice_days" type="number" value="${r.data.min_notice_days}"><div class="err" id="e_min_notice_days"></div><p><button type="submit">Save policies</button></p><p id="saved"></p></form>`}
 if(p==='/reports'){const r=await api('GET','/api/reports'); if(!r.ok) return forbidden(); const tab=new URLSearchParams(location.search).get('by')||'type';
  const rows=Object.entries(tab==='type'?r.data.by_type:r.data.by_department).map(([k,v])=>`<tr><td>${k}</td><td>${v}</td></tr>`).join('');
  return `<h1>Reports</h1><div class="tabs"><button role="tab" data-go="/reports?by=type" class="${tab==='type'?'on':''}">By leave type</button><button role="tab" data-go="/reports?by=department" class="${tab==='department'?'on':''}">By department</button></div>
  <div class="card"><table><thead><tr><th>${tab==='type'?'Leave type':'Department'}</th><th>${tab==='type'?'Days taken':'Requests'}</th></tr></thead><tbody>${rows}</tbody></table></div>`}
 if(p==='/profile'){return `<h1>Profile</h1><div class="card"><p>Signed in as <strong>${esc(ME.name)}</strong> (${ME.role}).</p></div>`}
 const l=await api('GET','/api/leave');
 return `<h1>Dashboard</h1><div class="card"><span class="stat"><b>${l.data.balance}</b>annual days left</span><span class="stat"><b>${l.data.requests.filter(r=>r.status==='Pending').length}</b>pending requests</span></div>`}
const forbidden=()=>'<h1>Forbidden</h1><div class="card">You do not have access to this page.</div>';
function leaveModal(){return `<div class="modal" role="dialog" aria-labelledby="mt"><div><h2 id="mt">Request leave</h2><form id="lf">
 <label for="type">Leave type</label><select id="type"><option value="">Choose…</option>${['Annual','Sick','Unpaid','Parental'].map(t=>`<option>${t}</option>`).join('')}</select><div class="err" id="e_type"></div>
 <label for="from">From</label><input id="from" type="date"><div class="err" id="e_from"></div><label for="to">To</label><input id="to" type="date"><div class="err" id="e_to"></div>
 <label for="reason">Reason</label><textarea id="reason" maxlength="400"></textarea><div class="err" id="e_reason"></div>
 <p><button type="button" class="sec" id="cancel">Cancel</button> <button type="submit">Submit request</button></p></form></div></div>`}
function empModal(){return `<div class="modal" role="dialog" aria-labelledby="mt"><div><h2 id="mt">Add employee</h2><form id="ef">
 <label for="name">Full name</label><input id="name"><div class="err" id="e_name"></div><label for="email">Work email</label><input id="email" type="email"><div class="err" id="e_email"></div>
 <label for="department">Department</label><select id="department"><option value="">Choose…</option>${['Engineering','Sales','Finance','Support'].map(t=>`<option>${t}</option>`).join('')}</select><div class="err" id="e_department"></div>
 <label for="role">Role</label><select id="role"><option>employee</option><option>manager</option></select><label for="start">Start date</label><input id="start" type="date">
 <p><button type="button" class="sec" id="cancel">Cancel</button> <button type="submit">Add employee</button></p></form></div></div>`}
function rejectModal(id){return `<div class="modal" role="dialog" aria-labelledby="mt"><div><h2 id="mt">Reject request</h2><form id="rf" data-id="${id}">
 <label for="comment">Comment for the employee</label><textarea id="comment"></textarea><div class="err" id="e_comment"></div>
 <p><button type="button" class="sec" id="cancel">Cancel</button> <button type="submit" class="danger">Reject request</button></p></form></div></div>`}
function showErrors(errs){document.querySelectorAll('.err').forEach(e=>e.textContent='');for(const [k,v] of Object.entries(errs||{})){const e=$('#e_'+k);if(e)e.textContent=v}}
async function render(){const app=$('#app');
 if(!ME){const r=await fetch('/api/me');if(r.ok)ME=await r.json()}
 if(!ME){ if(location.pathname!=='/login')history.replaceState({},'','/login');
  app.innerHTML=`<main style="max-width:380px"><h1>Sign in to LeaveTrack</h1><form class="card" id="lg"><label for="email">Email</label><input id="email" type="email"><label for="password">Password</label><input id="password" type="password">
  <div id="codebox" hidden><label for="code">Verification code</label><input id="code" autocomplete="one-time-code"></div><div class="err" id="lerr" role="alert"></div><p><button type="submit">Sign in</button></p></form></main>`;
  $('#lg').onsubmit=async e=>{e.preventDefault();const body={email:$('#email').value,password:$('#password').value,code:$('#code').value};
   const r=await api('POST','/api/login',body); if(r.data.need_code){$('#codebox').hidden=false;$('#lerr').textContent='Enter the code from your authenticator app';return}
   if(!r.ok){$('#lerr').textContent=r.data.detail;return} ME=r.data; go('/')}; return}
 if(location.pathname==='/login'){history.replaceState({},'','/')}
 app.innerHTML=shell(await page()); bind()}
function bind(){
 document.querySelectorAll('[data-nav]').forEach(a=>a.onclick=e=>{e.preventDefault();MENU=false;go(a.getAttribute('href'))});
 document.querySelectorAll('[data-go]').forEach(a=>a.onclick=()=>{MENU=false;go(a.dataset.go)});
 $('#acct').onclick=()=>{MENU=!MENU;render()}; const lo=$('#lo'); if(lo)lo.onclick=async()=>{await api('POST','/api/logout');ME=null;MENU=false;go('/login')};
 const close=()=>{MODAL=null;render()}; const c=$('#cancel'); if(c)c.onclick=close;
 const nw=$('#new'); if(nw)nw.onclick=()=>{MODAL=leaveModal();render()};
 const lf=$('#lf'); if(lf)lf.onsubmit=async e=>{e.preventDefault();const r=await api('POST','/api/leave',{type:$('#type').value,from:$('#from').value,to:$('#to').value,reason:$('#reason').value});if(!r.ok)return showErrors(r.data.errors);close()};
 const ae=$('#addemp'); if(ae)ae.onclick=()=>{MODAL=empModal();render()};
 const ef=$('#ef'); if(ef)ef.onsubmit=async e=>{e.preventDefault();const r=await api('POST','/api/employees',{name:$('#name').value,email:$('#email').value,department:$('#department').value,role:$('#role').value,start:$('#start').value});if(!r.ok)return showErrors(r.data.errors);close()};
 document.querySelectorAll('[data-approve]').forEach(b=>b.onclick=async()=>{await api('POST',`/api/team/${b.dataset.approve}/decision`,{decision:'Approved'});render()});
 document.querySelectorAll('[data-reject]').forEach(b=>b.onclick=()=>{MODAL=rejectModal(b.dataset.reject);render()});
 const rf=$('#rf'); if(rf)rf.onsubmit=async e=>{e.preventDefault();const r=await api('POST',`/api/team/${rf.dataset.id}/decision`,{decision:'Rejected',comment:$('#comment').value});if(!r.ok)return showErrors(r.data.errors);close()};
 const pol=$('#pol'); if(pol)pol.onsubmit=async e=>{e.preventDefault();const r=await api('PUT','/api/policies',{annual_days:$('#annual_days').value,carry_over_max:$('#carry_over_max').value,min_notice_days:$('#min_notice_days').value});showErrors(r.ok?{}:r.data.errors);$('#saved').textContent=r.ok?'Policies saved':''};
}
addEventListener('popstate',render);render();
</script></body></html>"""


@app.get("/{path:path}", response_class=HTMLResponse)
def spa(path: str) -> str:
    return PAGE
