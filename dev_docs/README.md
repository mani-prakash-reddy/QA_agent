# Development docs

Internal documents for building QA Agent. Product-facing documents live in `../docs/`.

| Document | Purpose |
|---|---|
| [PRD.md](PRD.md) | What we are building and why; requirements, users, metrics, scope |
| [TRD.md](TRD.md) | How it is built; stack, agent runtime, tool gate, APIs, events, decisions |
| [backend-schema.md](backend-schema.md) | Where data lives: tables, graph, vectors, Redis, artifacts |
| [app-flow.md](app-flow.md) | User journeys and screens |
| [design-brief.md](design-brief.md) | UI direction and components |
| [implementation-plan.md](implementation-plan.md) | Milestones, tasks, exit criteria, risks |
| [STATUS.md](STATUS.md) | Current progress, decisions, open questions |
| [spike-results.md](spike-results.md) | M0 findings that changed the design |
| [extraction-results.md](extraction-results.md) | Measured BRD extraction quality (M2) |
| [mapping-results.md](mapping-results.md) | Measured requirement↔screen mapping quality and its limits (M6) |
| [generation-results.md](generation-results.md) | Test generation quality, live replay results and limits (M7) |

Read order for newcomers: `../docs/architecture.md` → PRD → TRD → backend-schema → implementation-plan.
