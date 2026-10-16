"""Rule-based test generation: deterministic cases derived from what the crawler observed.

Field validation, boundaries, required/format negatives, positive form flows and role access. No LLM:
every value comes from a recorded constraint, every expected message from a recorded message when one
exists. Where the app showed no message, the expectation says so and the case is marked `inferred`."""

import re
import uuid
from typing import Any

from app.qa.generation.gather import FormCtx, RoleAccess
from app.qa.generation.schema import Action, CaseDraft, Step, Target, Value
from app.qa.mapping.paths import direct_route
from app.qa.models import FormField, UiState

SKIP_TYPES = {"checkbox", "radio", "hidden", "file", "submit", "button"}


def _num(text: str | None) -> float | None:
    try:
        return float(text) if text not in (None, "") else None
    except ValueError:
        return None


def _fmt(n: float) -> str:
    return str(int(n)) if float(n).is_integer() else str(n)


def valid_value(f: FormField) -> Value:
    t = f.input_type
    if t == "email":
        return Value(kind="literal", text="tester@example.com")
    if t == "password":
        return Value(kind="placeholder", text="valid password")
    if t in {"number", "range"}:
        lo, hi = _num(f.min_value), _num(f.max_value)
        if lo is not None and hi is not None:
            return Value(kind="literal", text=_fmt((lo + hi) // 2))
        return Value(kind="literal", text=_fmt(lo) if lo is not None else "10")
    if t == "tel":
        return Value(kind="literal", text="5551234567")
    if t == "url":
        return Value(kind="literal", text="https://example.com")
    if t == "date":
        return Value(kind="literal", text="2030-01-15")
    opts = (f.options or {}).get("values") if f.options else None
    if opts:
        return Value(kind="literal", text=str(opts[0]))
    text = "Sample text"
    if f.max_length and f.max_length < len(text):
        return Value(kind="repeat", char="a", count=max(f.min_length or 1, 1))
    if f.min_length and f.min_length > len(text):
        return Value(kind="repeat", char="a", count=f.min_length)
    return Value(kind="literal", text=text)


def _testable(f: FormField) -> bool:
    return bool(f.label) and f.input_type not in SKIP_TYPES and not f.readonly


def _message(f: FormField, *triggers: str) -> str | None:
    for m in f.validation_messages or []:
        if isinstance(m, dict) and m.get("trigger") in triggers and m.get("message"):
            return str(m["message"])
    return None


def open_steps(start_title: str, path: list[tuple[str, str]]) -> list[Step]:
    """Open the application, then press each control on the way to the screen."""
    steps = [Step(action="open_screen", screen=start_title)]
    for label, kind in path:
        if kind == "url":  # a page opened by its address replaces the start page
            steps = [Step(action="open_screen", screen=label)]
        else:
            steps.append(Step(action="click", target=Target(label=label, kind=kind)))
    return steps


def _setup(ctx: FormCtx) -> list[Step]:
    return open_steps(ctx.start_title, ctx.path)


def _fill(f: FormField, v: Value) -> Step:
    action: Action = "select" if f.input_type in {"select", "combobox"} else "fill"
    return Step(action=action, target=Target(label=f.label, kind="field"), value=v)


def _submit_step(ctx: FormCtx) -> Step | None:
    if ctx.form.submit_label:
        return Step(
            action="click", target=Target(label=ctx.form.submit_label, kind="button")
        )
    return None


def _base(ctx: FormCtx, skip: FormField | None) -> list[Step]:
    """Steps that fill every other required field with a valid value."""
    return [
        _fill(f, valid_value(f))
        for f in ctx.fields
        if _testable(f) and f.required and f is not skip
    ]


def _destructive_submit(ctx: FormCtx, destructive: set[str]) -> bool:
    lab = (ctx.form.submit_label or "").strip().lower()
    return bool(lab) and lab in destructive


def _case(
    ctx: FormCtx,
    req_key: str,
    title: str,
    test_type: str,
    priority: str,
    steps: list[Step],
    expected: str,
    role: str | None,
    evidence: dict[str, Any],
    *,
    precond_extra: list[str] | None = None,
) -> CaseDraft:
    pre = ["The application is reachable and the test environment is in a known state"]
    if role:
        pre.insert(
            0,
            f"The tester is signed in as the '{role}' role (use that role's test account)",
        )
    pre += precond_extra or []
    if ctx.needs_rows:
        pre.append(
            f"At least one row exists on '{ctx.parent_title}' (its '{ctx.opener}' control is per row): create one first if the list is empty"
        )
    return CaseDraft(
        requirement_key=req_key,
        title=title,
        test_type=test_type,
        priority=priority,
        role_name=role,
        module=ctx.state.module,
        state_id=ctx.state.id,
        preconditions=pre,
        steps=steps,
        expected_result=expected,
        origin="rule",
        evidence={
            "form": ctx.form.name or ctx.form.submit_label or "form",
            **({"needs_rows": True} if ctx.needs_rows else {}),
            **evidence,
        },
    )


def _machine_name(name: str) -> bool:
    """'my_account_form-d9c1d618', 'issue-form-896e78b3': an id the page generated, not a name a person knows."""
    return bool(
        re.search(r"[-_][0-9a-f]{6,}$", name) or re.fullmatch(r"[\w-]+_form", name)
    )


def form_cases(
    ctx: FormCtx, req_key: str, role: str | None, destructive: set[str]
) -> list[CaseDraft]:
    cases: list[CaseDraft] = []
    # The accessible name of a form is often its submit button's: name the screen instead.
    own = (
        ctx.form.name
        if ctx.form.name
        and ctx.form.name != ctx.form.submit_label
        and not _machine_name(ctx.form.name)
        else ""
    )
    form_name = own or ctx.parent_title
    where = f"on '{form_name}'"
    submit = _submit_step(ctx)
    no_submit_note = (
        ["This form's submit action is destructive: the case stops before submitting"]
        if _destructive_submit(ctx, destructive)
        else []
    )
    can_submit = submit is not None and not no_submit_note

    def finish(steps: list[Step]) -> list[Step]:
        # The submit click is part of the case unless the action is destructive.
        return steps + ([submit] if can_submit and submit else [])

    # ---- positive: all required fields valid
    required = [f for f in ctx.fields if _testable(f) and f.required]
    if required or any(_testable(f) for f in ctx.fields):
        fields = required or [f for f in ctx.fields if _testable(f)][:1]
        steps = _setup(ctx) + [_fill(f, valid_value(f)) for f in fields]
        if can_submit:
            cases.append(
                _case(
                    ctx,
                    req_key,
                    f"Submit the '{form_name}' form with valid data",
                    "positive",
                    "P1",
                    finish(steps),
                    "The form is accepted without validation errors and the application confirms the action",
                    role,
                    {"basis": "requirement", "rule": "positive_flow"},
                )
            )
    for f in ctx.fields:
        if not _testable(f):
            continue
        label = f.label
        ev = {"field": label, "input_type": f.input_type}
        # ---- required: left empty (a dropdown cannot be cleared by a tester, so it is not tested this way)
        if f.required and f.input_type not in {"select", "combobox"}:
            msg = _message(f, "empty")
            steps = (
                _setup(ctx)
                + _base(ctx, f)
                + [Step(action="clear", target=Target(label=label, kind="field"))]
            )
            expected = (
                f"The message \"{msg}\" is shown for '{label}' and the form is not submitted"
                if msg
                else f"A validation error is shown for '{label}' and the form is not submitted"
            )
            cases.append(
                _case(
                    ctx,
                    req_key,
                    f"{label} is required {where}",
                    "field_validation",
                    "P2",
                    finish(steps),
                    expected,
                    role,
                    {
                        **ev,
                        "basis": "observed" if msg else "inferred",
                        "rule": "required",
                    },
                    precond_extra=no_submit_note,
                )
            )
        # ---- format: email
        if f.input_type == "email":
            msg = _message(f, "invalid_format", "empty")
            steps = (
                _setup(ctx)
                + _base(ctx, f)
                + [
                    Step(
                        action="fill",
                        target=Target(label=label, kind="field"),
                        value=Value(kind="literal", text="not-an-email"),
                    )
                ]
            )
            cases.append(
                _case(
                    ctx,
                    req_key,
                    f"{label} rejects an invalid email format {where}",
                    "negative",
                    "P2",
                    finish(steps),
                    f"The message \"{msg}\" is shown for '{label}' and the form is not submitted"
                    if msg
                    else f"A validation error is shown for '{label}' and the form is not submitted",
                    role,
                    {
                        **ev,
                        "basis": "observed" if msg else "inferred",
                        "rule": "email_format",
                    },
                    precond_extra=no_submit_note,
                )
            )
        # ---- length boundaries
        if f.max_length:
            n = f.max_length
            steps = (
                _setup(ctx)
                + _base(ctx, f)
                + [
                    Step(
                        action="fill",
                        target=Target(label=label, kind="field"),
                        value=Value(kind="repeat", char="a", count=n),
                    )
                ]
            )
            cases.append(
                _case(
                    ctx,
                    req_key,
                    f"{label} accepts exactly {n} characters {where}",
                    "boundary",
                    "P3",
                    finish(steps),
                    f"The {n}-character value is accepted for '{label}' with no length error",
                    role,
                    {**ev, "basis": "inferred", "rule": "max_length_at", "limit": n},
                    precond_extra=no_submit_note,
                )
            )
            steps = (
                _setup(ctx)
                + _base(ctx, f)
                + [
                    Step(
                        action="fill",
                        target=Target(label=label, kind="field"),
                        value=Value(kind="repeat", char="a", count=n + 1),
                    )
                ]
            )
            msg = _message(f, "too_long")
            cases.append(
                _case(
                    ctx,
                    req_key,
                    f"{label} rejects {n + 1} characters {where}",
                    "boundary",
                    "P3",
                    finish(steps),
                    f"The message \"{msg}\" is shown for '{label}' and the form is not submitted"
                    if msg
                    else f"'{label}' does not accept more than {n} characters: the input is limited or a length error is shown, and the form is not submitted with {n + 1} characters",
                    role,
                    {
                        **ev,
                        "basis": "observed" if msg else "inferred",
                        "rule": "max_length_over",
                        "limit": n,
                    },
                    precond_extra=no_submit_note,
                )
            )
        if f.min_length and f.min_length > 1:
            n = f.min_length
            steps = (
                _setup(ctx)
                + _base(ctx, f)
                + [
                    Step(
                        action="fill",
                        target=Target(label=label, kind="field"),
                        value=Value(kind="repeat", char="a", count=n - 1),
                    )
                ]
            )
            cases.append(
                _case(
                    ctx,
                    req_key,
                    f"{label} rejects {n - 1} characters (minimum {n}) {where}",
                    "boundary",
                    "P3",
                    finish(steps),
                    f"A validation error is shown for '{label}' and the form is not submitted",
                    role,
                    {**ev, "basis": "inferred", "rule": "min_length_under", "limit": n},
                    precond_extra=no_submit_note,
                )
            )
        # ---- numeric range
        lo, hi = _num(f.min_value), _num(f.max_value)
        if f.input_type in {"number", "range"} and (lo is not None or hi is not None):
            probes: list[tuple[float, bool, str]] = []
            if lo is not None:
                probes += [
                    (lo - 1, False, "below the minimum"),
                    (lo, True, "the minimum"),
                ]
            if hi is not None:
                probes += [
                    (hi, True, "the maximum"),
                    (hi + 1, False, "above the maximum"),
                ]
            for val, ok, name in probes:
                steps = (
                    _setup(ctx)
                    + _base(ctx, f)
                    + [
                        Step(
                            action="fill",
                            target=Target(label=label, kind="field"),
                            value=Value(kind="literal", text=_fmt(val)),
                        )
                    ]
                )
                cases.append(
                    _case(
                        ctx,
                        req_key,
                        f"{label}: {_fmt(val)} ({name}) {where}",
                        "boundary",
                        "P3",
                        finish(steps),
                        f"The value {_fmt(val)} is accepted for '{label}'"
                        if ok
                        else f"A validation error is shown for '{label}' and the form is not submitted",
                        role,
                        {
                            **ev,
                            "basis": "inferred",
                            "rule": "numeric_boundary",
                            "min": lo,
                            "max": hi,
                        },
                        precond_extra=no_submit_note,
                    )
                )
        # ---- listed options
        vals = (f.options or {}).get("values") if f.options else None
        if vals and not (f.options or {}).get("truncated"):
            steps = _setup(ctx) + [
                Step(
                    action="verify",
                    expect=f"the '{label}' field offers exactly these options: {', '.join(map(str, vals))}",
                )
            ]
            cases.append(
                _case(
                    ctx,
                    req_key,
                    f"{label} offers the expected options {where}",
                    "positive",
                    "P3",
                    steps,
                    f"'{label}' lists: {', '.join(map(str, vals))}",
                    role,
                    {**ev, "basis": "observed", "rule": "option_list"},
                )
            )
    return cases


def role_cases(
    state: UiState,
    req_key: str,
    access: RoleAccess,
    path: list[tuple[str, str]],
    start_title: str,
    group: list[uuid.UUID] | None = None,
    role_paths: dict[str, list[tuple[str, str]]] | None = None,
) -> list[CaseDraft]:
    """Who can open a screen. A role the crawl never saw it for is *not observed*, not proven denied."""
    out: list[CaseDraft] = []
    title = state.heading or state.title or " > ".join(label for label, _ in path)
    # A page whose controls differ by role is several recorded states of one route: a role can open
    # the screen if it reached any of them.
    ids = group or [state.id]
    visible = set().union(*(access.visible.get(i, set()) for i in ids))
    denied = set().union(*(access.denied.get(i, set()) for i in ids))
    for rid in sorted(visible, key=lambda r: access.roles.get(r, "")):
        role = access.roles.get(rid, "")
        rpath = (role_paths or {}).get(role, path)
        out.append(
            CaseDraft(
                requirement_key=req_key,
                title=f"{role} can open '{title}'",
                test_type="role_access",
                priority="P2",
                role_name=role,
                module=state.module,
                state_id=state.id,
                preconditions=[
                    f"The tester is signed in as the '{role}' role (use that role's test account)"
                ],
                steps=[
                    *open_steps(start_title, rpath),
                    Step(action="verify", expect=f"the '{title}' screen is displayed"),
                ],
                expected_result=f"The '{title}' screen opens for the '{role}' role",
                evidence={"basis": "observed", "rule": "role_visible"},
            )
        )
    # A role is only said to lack access if its crawl finished: a budget stop proves nothing.
    candidates = ((set(access.complete) & set(access.seen_any)) | denied) - visible
    for rid in sorted(candidates, key=lambda r: access.roles.get(r, "")):
        role = access.roles.get(rid, "")
        proven = rid in denied
        route = direct_route(state)
        if proven and route:
            # Observed: this role was sent away when it opened the address. Reproduce exactly that.
            steps = [
                Step(action="open_screen", screen=route),
                Step(
                    action="verify",
                    expect="access is denied, the user is redirected, or the screen is not found",
                ),
            ]
        else:
            steps = [
                Step(
                    action="verify",
                    expect=f"no navigation control to '{title}' is offered to this role",
                ),
                *open_steps(start_title, path),
                Step(
                    action="verify",
                    expect="access is denied, the user is redirected, or the screen is not found",
                ),
            ]
        out.append(
            CaseDraft(
                requirement_key=req_key,
                title=f"{role} cannot open '{title}'",
                test_type="role_access",
                priority="P2" if proven else "P3",
                role_name=role,
                module=state.module,
                state_id=state.id,
                preconditions=[
                    f"The tester is signed in as the '{role}' role (use that role's test account)"
                ],
                steps=steps,
                expected_result=f"The '{role}' role is not able to use '{title}'",
                evidence={
                    "basis": "observed" if proven else "not_observed",
                    "rule": "role_denied",
                    "note": None
                    if proven
                    else "Another role reached this screen during the crawl and this role did not; the denial was not probed, so confirm the intended permission",
                },
            )
        )
    return out


def clip(cases: list[CaseDraft], limit: int) -> tuple[list[CaseDraft], int]:
    rank = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}
    ordered = sorted(
        cases, key=lambda c: rank.get(c.priority, 9)
    )  # stable: keeps field order
    return ordered[:limit], max(0, len(ordered) - limit)


__all__ = ["clip", "form_cases", "role_cases", "valid_value"]
