# Demo script (about 10 minutes)

Prerequisites: the stack is up (`docker compose up -d`), model keys are in `.env`, and you have a
test account in the demo app (register it yourself in Juice Shop, or use the template app's users).

## A. Template app (fastest; no extra container)

1. **New project** `template-app`, URL `http://localhost:5173`, tick *test or staging environment*.
2. **Roles**: add `Admin` and `User` with accounts that exist in the template app.
3. **Requirements → Upload BRD**: `demo/brds/template-app.md` (or the `.docx` / `.pdf`). Watch the run
   page: sections parsed, requirements appearing with a source quote each. One requirement is
   flagged (vague); open it, see the reason, and either rewrite it or *Accept wording as written*.
   Select all and approve.
4. **Crawl → Start crawl** (use a host worker for this app: its browser calls the API on `localhost`).
   Watch the live screenshot and the agent per role; no data is changed (see *Crawl log* for what
   was blocked or skipped).
5. **Mapping → Run mapping**. Open *Requirement gaps* (BRD items the app does not implement) and
   *Undocumented screens* (features the BRD never mentions). Reject a link you disagree with and link
   a screen by hand; the gaps update at once.
6. **Test cases → Generate test cases**. Open a case: steps with real labels, test data, expected
   result and **how that result is known** (observed / inferred / from the requirement). Edit one
   (new version appears under *History*), approve a few in bulk, then *Regenerate…* a requirement
   with a comment.
7. **Traceability matrix**: counts reconcile with the table; every uncovered requirement says why.
8. **Export**: Excel for test cases and the RTM, CSV for the gap report. **Template**: rename a
   column and change the ID prefix; export again.
9. **Metrics**: time and tokens per run.

## B. Juice Shop (a bigger, messier SPA)

```bash
docker compose --profile demo-juice up -d juice-shop
```
Register a customer (and, if you want a second role, an admin) in the shop at `http://localhost:3000`,
create a project with URL `http://juice-shop:3000`, add the roles, upload `demo/brds/juice-shop.pdf`,
and follow steps 3-8. The crawl is bounded (the evaluation used 120 steps per role), so some requirements will show as
gaps only because the crawl did not reach them: the Mapping tab warns when that is the case.

## C. Redmine and Gitea (server-rendered apps, role denial)

```bash
docker compose --profile demo-redmine up -d redmine      # http://localhost:3001
docker compose --profile demo-gitea up -d gitea          # http://localhost:3002
```
Redmine: load its default data once (`docker exec -e REDMINE_LANG=en <container> bundle exec rake
redmine:load_default_data`), set the admin password (it forces a change on first login), add a *Developer*
user and a project that user belongs to. Gitea: `gitea admin user create` for an administrator and a normal
user; create a repository, an issue and an organisation as the normal user so there is something to test.
Add the roles in the UI (use the host URL if the worker runs on the host), upload `demo/brds/redmine.md` or
`gitea.md`, crawl. In **Roles / permission matrix** look for the red words (forbidden, redirected): those
are screens another role reached that this role was *observed* being refused when it opened them by address.

## D. Login challenges (try them on your own app)

Add the role with *Password + authenticator code* (paste the base32 secret), or *a code I type during the
crawl* (the run page then shows a prompt), or *Saved browser session* (paste a Playwright storage-state
JSON). A session that expires mid-crawl is renewed automatically; a CAPTCHA fails the role with a message
saying so.

## Things to point out

- Nothing is clicked that looks destructive and no form is submitted: the network guard blocks writes
  even if a click slips through (Crawl log lists them).
- A requirement the BRD states vaguely is never turned into a confident test.
- Every test step names a control that exists in the recorded application; cases that would need a
  control the crawler never found are not generated (the run summary counts them).
