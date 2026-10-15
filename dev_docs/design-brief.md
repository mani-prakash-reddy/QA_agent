# Design Brief

Brief document. The UI reuses the existing template's design system, so the goal is consistency and fast delivery, not a new visual identity.

## 1. Principles
1. **Transparency over polish** — users must trust what the agents did; show evidence (screenshots, quotes, citations) next to every claim.
2. **Dense but scannable** — QA work is table-heavy; prioritize filters, sorting, keyboard-friendly review.
3. **Live but calm** — streaming content should not jump; auto-scroll can be paused.
4. **Safe by default** — destructive/expensive actions (disable safe mode, start a costly run) need explicit confirmation.

## 2. Foundation (reuse)
- Components: shadcn/ui (button, card, dialog, sheet, tabs, table, badge, select, form, tooltip, skeleton, sonner toast) already in `frontend/src/components/ui`.
- Tables: existing `DataTable` (TanStack Table) with columns pattern from `Items/columns.tsx`.
- Layout: existing `_layout` with `AppSidebar`; add project-scoped sub-navigation as tabs.
- Theme: existing light/dark theme provider; no new palette. Status colors come from semantic tokens.
- Icons: lucide (already used).

## 3. New UI building blocks

| Component | Purpose |
|---|---|
| `StatusBadge` | Run/agent/test/requirement status with consistent color + icon (never color alone) |
| `AgentTree` | Collapsible supervisor → sub-agent tree with status dot, role/module chip, budget bar |
| `EventFeed` | Virtualized, filterable event list; types styled distinctly (reasoning, tool call, result, skip, error); pause auto-scroll |
| `ScreenshotPanel` | Latest screenshot per agent with step caption; click for full size; mini filmstrip |
| `CoverageCounters` | States / forms / fields / skipped / blocked counters with live increments |
| `CostMeter` | Tokens + cost, per agent and total, vs cap |
| `SourceQuote` | Requirement's BRD quote with page/section link |
| `RoleMatrix` | Grid roles × states/actions with allowed/forbidden/not-observed cells |
| `GraphView` | Navigation graph (lightweight lib, e.g. React Flow/Cytoscape — choose in M6); filter by role/module |
| `TestCaseDrawer` | Rendered steps, grounding links to KM states, version history, edit mode |
| `RtmGrid` | Requirement × test-type counts with heat-style emphasis for zero coverage |
| `ConfigForm` | Structured forms for crawl/generation/template config with sensible defaults and "advanced" collapse |

## 4. Key screen layouts

**Live Run (the showpiece)** — three columns on desktop, stacked on narrow screens:
```
┌ Run header: status · elapsed · cost · [Pause] [Stop] ─────────────────────────┐
│ Agent tree (left, 20%) │ Event feed for selected agent (center, 45%) │ Browser  │
│ ▸ Supervisor           │ 10:01 plan: explore Admin→Users            │ screenshot│
│   ● Explorer Admin/Users│ 10:01 tool browser_click "Add User"        │ + caption │
│   ● Explorer User/Items │ 10:02 state discovered: Add User (modal)   │───────────│
│   ○ Test author (queued)│ 10:02 blocked: "Delete" (safe mode)        │ Counters  │
│                        │                                            │ Cost meter│
└────────────────────────┴────────────────────────────────────────────┴──────────┘
```
Prompt cards (e.g., "Enter OTP for Admin") appear pinned above the feed.

**Test review** — table with sticky filters (requirement, type, role, status, text search); row click opens a right-side drawer; bulk select → approve/reject/regenerate; keyboard `J/K` next/prev, `A` approve, `R` reject.

**Requirements** — table with ambiguity badges; row expands to show the source quote and flag reasons; inline edit.

## 5. Content and tone
- Use the application's own terms (project, role, state → shown as "screen", snapshot → "app model").
- Explain flags and blocks with a reason, never just an icon.
- Empty states say what to do next ("Upload a BRD to extract requirements").

## 6. Accessibility and responsiveness
- Keyboard navigable tables and drawers; focus management in dialogs/drawers.
- Status never conveyed by color alone; sufficient contrast in both themes.
- Desktop-first (QA tooling); tables and the live view remain usable at tablet width; mobile is read-only best effort.
- Live regions: announce run status changes, not every event.

## 7. Performance
- Virtualize EventFeed and large tables (≥500 rows).
- Throttle `llm_token` rendering (batch per animation frame).
- Screenshots lazy-loaded, thumbnails in filmstrip.

## 8. Out of scope for design
Custom branding, marketing pages, mobile-first layouts, bespoke illustrations.
