"""Write the BRDs, requirement gold files and mapping gold files of the purpose-built test apps from one
source of truth (so the text, the expected extraction and the expected mapping cannot drift apart).

    python3 demo/build_app_brds.py        # then: cd backend && uv run python ../demo/build_sample_brds.py (docx/pdf)

Each requirement: (key, type, text, match phrase that must appear in the extracted requirement,
flagged-as-ambiguous?, expected mapping: list of 'route|title fragment' or 'gap' or None for not scored)."""

import json
from pathlib import Path

HERE = Path(__file__).parent / "brds"

Req = tuple[str, str, str, str, bool, list[str] | str | None]

APPS: dict[str, dict] = {
    "leavetrack": {
        "undocumented": ["/profile"],
        "title": "LeaveTrack Leave Management: Business Requirements Document",
        "purpose": "LeaveTrack lets employees request leave, managers decide on their team's requests, and HR administer employees and leave policies.",
        "scope": "In scope: sign-in, leave requests, approvals, employee records, leave policies, reports. Out of scope: payroll and calendar integrations.",
        "roles": [
            ("Employee", "Any staff member", "Own leave: dashboard, request leave, history"),
            ("Manager", "Line manager", "Everything an Employee can do, plus decisions on pending requests"),
            ("HR", "Human resources", "Everything a Manager can do, plus employees, leave policies and reports. Signs in with a second factor"),
        ],
        "sections": [
            ("Sign-in", [
                ("FR-1", "functional", "A user shall sign in with an email address and a password.", "sign in with an email address", False, ["/login|"]),
                ("FR-2", "functional", "HR users shall also enter a six-digit verification code from an authenticator app when they sign in.", "six-digit verification code", False, ["/login|"]),
                ("FR-3", "functional", "A signed-in user shall be able to sign out from the account menu.", "sign out from the account menu", False, None),
                ("FR-4", "functional", "A user who is not signed in shall be sent to the sign-in page.", "sent to the sign-in page", False, None),
            ]),
            ("Leave requests", [
                ("FR-5", "functional", "The dashboard shall show the employee's remaining annual leave days and the number of pending requests.", "remaining annual leave days", False, ["/|dashboard", "/|"]),
                ("FR-6", "functional", "An employee shall request leave by choosing a leave type (Annual, Sick, Unpaid or Parental), a start date, an end date and an optional reason.", "choosing a leave type", False, ["/leave|my leave"]),
                ("FR-7", "functional", "The employee shall see their leave history with type, dates, days and status.", "leave history with type, dates, days and status", False, ["/leave|my leave"]),
                ("BR-1", "business_rule", "The end date of a request cannot be before its start date.", "end date of a request cannot be before", False, ["/leave|my leave"]),
                ("BR-2", "business_rule", "The reason of a request must be at most 200 characters.", "reason of a request must be at most 200", False, ["/leave|my leave"]),
                ("BR-3", "business_rule", "An annual leave request cannot use more days than the remaining balance; the employee is told how many days are left.", "cannot use more days than the remaining balance", False, ["/leave|my leave"]),
                ("FR-8", "functional", "Leave requests shall be easy to submit.", "easy to submit", True, None),
            ]),
            ("Approvals", [
                ("FR-9", "functional", "A manager shall see the pending requests and approve or reject each one.", "approve or reject each one", False, ["/team|team requests"]),
                ("BR-4", "business_rule", "Rejecting a request requires a comment of at least five characters.", "requires a comment of at least five characters", False, ["/team|team requests"]),
                ("FR-10", "functional", "Employees shall be notified by email when their request is decided.", "notified by email when their request is decided", False, "gap"),
            ]),
            ("Employees and policies (HR)", [
                ("FR-11", "functional", "HR shall see the list of employees with name, email, department and role.", "list of employees with name, email, department and role", False, ["/employees|employees"]),
                ("FR-12", "functional", "HR shall add an employee by entering a full name, a work email, a department, a role and a start date.", "add an employee by entering a full name", False, ["/employees|employees"]),
                ("BR-5", "business_rule", "An employee's name is required and at most 60 characters; the work email must be a valid, unused address.", "name is required and at most 60 characters", False, ["/employees|employees"]),
                ("FR-13", "functional", "HR shall set the annual leave days, the maximum carry-over days and the minimum notice in days.", "set the annual leave days", False, ["/policies|leave policies"]),
                ("BR-6", "business_rule", "Annual leave days must be a whole number from 0 to 60, carry-over from 0 to 10, and notice from 0 to 30.", "whole number from 0 to 60", False, ["/policies|leave policies"]),
                ("FR-14", "functional", "HR shall see leave taken by leave type and by department.", "leave taken by leave type and by department", False, ["/reports|reports"]),
                ("FR-15", "functional", "HR shall export the employee list to a spreadsheet.", "export the employee list to a spreadsheet", False, "gap"),
            ]),
            ("Access", [
                ("FR-16", "functional", "Only managers and HR shall see the Team requests page; only HR shall see Employees, Leave policies and Reports.", "Only managers and HR shall see the Team requests page", False, None),
                ("FR-17", "functional", "A user shall be able to edit their profile picture.", "edit their profile picture", False, "gap"),
            ]),
        ],
        "nfr": [
            ("NFR-1", "nfr", "The system should be intuitive for first-time users.", "intuitive for first-time users", True, None),
            ("NFR-2", "nfr", "Every page shall load within 2 seconds for up to 500 employees.", "load within 2 seconds for up to 500 employees", False, "gap"),
        ],
    },
    "stockroom": {
        "undocumented": [],
        "title": "Stockroom Inventory: Business Requirements Document",
        "purpose": "Stockroom is the internal tool a small warehouse uses to keep products, stock levels, orders and suppliers up to date.",
        "scope": "In scope: sign-in, products, the new-product wizard, orders, suppliers, settings, exports. Out of scope: invoicing and barcode scanners.",
        "roles": [
            ("Admin", "Warehouse owner", "Everything, including suppliers, settings and deleting products"),
            ("Clerk", "Warehouse staff", "Products and orders; cannot delete products, see suppliers or change settings"),
            ("Viewer", "Read-only staff", "Can look at products and orders; no screen offers a change"),
        ],
        "sections": [
            ("Sign-in", [
                ("FR-1", "functional", "A user shall sign in with an email address and a password; wrong details show an error.", "sign in with an email address and a password", False, ["/login|"]),
                ("FR-2", "functional", "A signed-in user shall be able to log out at any time.", "log out at any time", False, None),
            ]),
            ("Products", [
                ("FR-3", "functional", "The dashboard shall show the number of products, how many are low on stock and how many orders are new.", "number of products, how many are low on stock", False, ["/|dashboard", "/|"]),
                ("FR-4", "functional", "Users shall list products with SKU, name, category, price and stock, five per page, and move between pages.", "five per page", False, ["/products|products", "/products?category&page&q|"]),
                ("FR-5", "functional", "Users shall search products by name and filter them by category.", "search products by name and filter them by category", False, ["/products|products", "/products?category&page&q|"]),
                ("FR-6", "functional", "Stock at or below the low-stock threshold shall be highlighted in the product list.", "highlighted in the product list", False, ["/products|products", "/products?category&page&q|"]),
                ("FR-7", "functional", "Opening a product shall show its details, its stock history and its supplier in separate tabs.", "details, its stock history and its supplier in separate tabs", False, ["/products/:id|", "/products/:id?tab|"]),
                ("FR-8", "functional", "Admins and clerks shall create a product in three steps: basics, price and stock, review.", "create a product in three steps", False, ["/products/new/:id|"]),
                ("BR-1", "business_rule", "A product name is required and at most 80 characters.", "product name is required and at most 80 characters", False, ["/products/new/:id|", "/products/:id/edit|"]),
                ("BR-2", "business_rule", "A SKU must look like SKU-1234 and be unique.", "SKU must look like SKU-1234", False, ["/products/new/:id|", "/products/:id/edit|"]),
                ("BR-3", "business_rule", "A price must be a number greater than 0 and stock a whole number from 0 to 100000.", "price must be a number greater than 0", False, ["/products/:id/edit|"]),
                ("FR-9", "functional", "Admins and clerks shall edit a product's name, SKU, category, price and stock.", "edit a product's name, SKU, category, price and stock", False, ["/products/:id/edit|"]),
                ("FR-10", "functional", "Only admins shall delete a product, after a confirmation page that names the product.", "delete a product, after a confirmation page", False, None),
                ("FR-11", "functional", "Products shall be uploaded in bulk from a spreadsheet.", "uploaded in bulk from a spreadsheet", False, "gap"),
            ]),
            ("Orders and suppliers", [
                ("FR-12", "functional", "Users shall list orders and filter them by status (New, Packed, Shipped).", "filter them by status", False, ["/orders|orders", "/orders?status|"]),
                ("FR-13", "functional", "Opening an order shall show the customer, the total and the status.", "show the customer, the total and the status", False, ["/orders/:id|"]),
                ("FR-14", "functional", "Admins and clerks shall change an order's status.", "change an order's status", False, ["/orders/:id|"]),
                ("FR-15", "functional", "Admins shall list suppliers and add a supplier with a name and a valid contact email.", "add a supplier with a name and a valid contact email", False, ["/suppliers|suppliers"]),
                ("FR-16", "functional", "Orders shall be emailed to the customer when they ship.", "emailed to the customer when they ship", False, "gap"),
            ]),
            ("Settings", [
                ("FR-17", "functional", "Admins shall set the low-stock threshold (a whole number from 0 to 1000) and the display currency (EUR, USD or GBP).", "set the low-stock threshold", False, ["/settings|settings"]),
            ]),
            ("Access", [
                ("FR-18", "functional", "Viewers shall not be offered any control that changes data.", "not be offered any control that changes data", False, None),
                ("FR-19", "functional", "Clerks shall not see the Suppliers and Settings pages.", "not see the Suppliers and Settings pages", False, None),
            ]),
        ],
        "nfr": [
            ("NFR-1", "nfr", "The product list should feel fast.", "feel fast", True, None),
            ("NFR-2", "nfr", "The product list shall load within 1 second for up to 10,000 products.", "within 1 second for up to 10,000 products", False, "gap"),
        ],
    },
    "helpdesk": {
        "undocumented": [],
        "title": "HelpDesk Support Tickets: Business Requirements Document",
        "purpose": "HelpDesk lets customers raise support tickets and lets agents and administrators work through them within agreed response times.",
        "scope": "In scope: sign-in and session handling, tickets, comments, help articles, SLA targets, users. Out of scope: live chat and phone integration.",
        "roles": [
            ("Customer", "A person who needs help", "Own tickets only: create, read, comment; help articles"),
            ("Agent", "Support staff", "All tickets: comment, assign to themselves, change status; help articles"),
            ("Admin", "Support lead", "Everything an Agent can do, plus SLA targets and the user list"),
        ],
        "sections": [
            ("Sign-in and sessions", [
                ("FR-1", "functional", "A user shall sign in with an email address and a password; wrong details show an error.", "sign in with an email address and a password", False, ["#/login|"]),
                ("FR-2", "functional", "A session shall expire after a period of inactivity and the user shall be returned to the sign-in page.", "session shall expire after a period of inactivity", False, None),
                ("FR-3", "functional", "A signed-in user shall be able to sign out.", "able to sign out", False, None),
            ]),
            ("Tickets", [
                ("FR-4", "functional", "Customers shall see only their own tickets; agents and admins shall see all tickets.", "see only their own tickets", False, ["#/tickets|"]),
                ("FR-5", "functional", "Users shall filter the ticket list by status (Open, In progress, Resolved, Closed) and search tickets by subject.", "filter the ticket list by status", False, ["#/tickets|", "#/tickets?status|"]),
                ("FR-6", "functional", "A customer shall create a ticket with a subject, a category, a priority, a description and an optional attachment.", "create a ticket with a subject, a category", False, ["#/tickets/new|"]),
                ("BR-1", "business_rule", "A ticket subject is required and at most 120 characters.", "ticket subject is required and at most 120 characters", False, ["#/tickets/new|"]),
                ("BR-2", "business_rule", "A ticket description must be at least 20 characters long.", "description must be at least 20 characters", False, ["#/tickets/new|"]),
                ("BR-3", "business_rule", "A ticket must have a category (Billing, Technical, Account or Other) and a priority (Low, Normal, High or Urgent).", "must have a category", False, ["#/tickets/new|"]),
                ("FR-7", "functional", "Opening a ticket shall show its status, priority, category, assignee and the whole conversation.", "status, priority, category, assignee and the whole conversation", False, ["#/tickets/:id|"]),
                ("FR-8", "functional", "Users shall add comments to a ticket they can see; an empty comment is refused.", "add comments to a ticket they can see", False, ["#/tickets/:id|"]),
                ("FR-9", "functional", "Agents and admins shall change a ticket's status and assign it to themselves.", "change a ticket's status and assign it to themselves", False, ["#/tickets/:id|"]),
                ("FR-10", "functional", "Customers shall rate the support they received once a ticket is closed.", "rate the support they received", False, "gap"),
                ("FR-11", "functional", "Ticket handling should be efficient.", "handling should be efficient", True, None),
            ]),
            ("Help articles, SLA and users", [
                ("FR-12", "functional", "Every user shall search the help articles by title.", "search the help articles by title", False, ["#/kb|"]),
                ("FR-13", "functional", "Admins shall set the first-response target in hours for each priority.", "first-response target in hours for each priority", False, ["#/sla|"]),
                ("BR-4", "business_rule", "A first-response target must be a whole number of hours from 1 to 240.", "whole number of hours from 1 to 240", False, ["#/sla|"]),
                ("FR-14", "functional", "Admins shall see the list of users with name, email and role.", "list of users with name, email and role", False, ["#/users|"]),
                ("FR-15", "functional", "Admins shall invite a new agent by email.", "invite a new agent by email", False, "gap"),
            ]),
            ("Access", [
                ("FR-16", "functional", "Only admins shall see the SLA targets and Users pages; customers shall see New ticket and My tickets.", "Only admins shall see the SLA targets and Users pages", False, None),
            ]),
        ],
        "nfr": [
            ("NFR-1", "nfr", "The application should be pleasant to use.", "pleasant to use", True, None),
            ("NFR-2", "nfr", "Tickets shall be stored for at least 7 years.", "stored for at least 7 years", False, "gap"),
        ],
    },
}


def write(name: str, app: dict) -> None:
    lines = [f"# {app['title']}", "", "## 1. Introduction", "", "### 1.1 Purpose", app["purpose"], "", "### 1.2 Scope", app["scope"], "",
             "## 2. Users and Roles", "", "| Role | Description | Access |", "|---|---|---|"]
    lines += [f"| {r} | {d} | {a} |" for r, d, a in app["roles"]]
    lines += ["", "## 3. Functional Requirements", ""]
    reqs: list[Req] = []
    for n, (title, items) in enumerate(app["sections"], 1):
        lines += [f"### 3.{n} {title}", ""]
        for key, typ, text, match, flagged, expect in items:
            lines += [f"{key}: {text}", ""]
            reqs.append((key, typ, text, match, flagged, expect))
    lines += ["## 4. Non-functional Requirements", ""]
    for key, typ, text, match, flagged, expect in app["nfr"]:
        lines += [text if flagged else f"{key}: {text}", ""]
        reqs.append((key, typ, text, match, flagged, expect))
    (HERE / f"{name}.md").write_text("\n".join(lines))
    gold = {
        "document": name,
        "note": "Expected requirements. `match` is a phrase that must appear in the extracted requirement. `flagged` = vague, must be flagged, not guessed.",
        # a vague requirement written without an id has no native key to match on
        "requirements": [{"key": None if (f and t == "nfr") else k, "type": t, "match": m, "flagged": f} for k, t, _x, m, f, _e in reqs],
    }
    (HERE / f"{name}.gold.json").write_text(json.dumps(gold, indent=2))
    mapping: dict[str, dict] = {}
    for k, t, _x, _m, f, e in reqs:
        if f and t == "nfr":
            continue
        if f:
            mapping[k] = {"ambiguous": True, "why": "Vague by design"}
        elif e == "gap":
            mapping[k] = {"expect": "gap"}
        elif e is None:
            mapping[k] = {"ambiguous": True, "why": "An access rule, a session rule or a menu action: needs the role matrix or a behaviour, not a screen"}
        else:
            mapping[k] = {"expect": "match", "screens": e}
    (HERE / f"{name}.mapping-gold.json").write_text(json.dumps({
        "note": "Written by the developer from the app's source: the screens are the ones a complete crawl reaches. `expect` = match | gap; `ambiguous` requirements are not scored.",
        "snapshot": f"{name} test app, complete crawl", "requirements": mapping,
        "undocumented_screens": {"routes": app.get("undocumented", [])}}, indent=2))
    print(name, len(reqs), "requirements")


if __name__ == "__main__":
    for n, a in APPS.items():
        write(n, a)
