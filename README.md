# QA Agent

FastAPI + React project (started from the Full Stack FastAPI Template).

- **Backend:** FastAPI, SQLModel, Alembic, JWT auth.
- **Database:** PostgreSQL 18 with pgvector, Apache AGE and JSONB.
- **Frontend:** React, TypeScript, Vite, Tailwind, shadcn/ui, generated API client.
- **Dev tooling:** Docker Compose with `watch`, Adminer, Playwright, pytest.

## Quick start

```bash
cp .env.example .env
docker compose watch
```

Frontend <http://localhost:5173> · API docs <http://localhost:8000/docs> · Adminer <http://localhost:8080>

See [development.md](./development.md), [backend/README.md](./backend/README.md) and [frontend/README.md](./frontend/README.md).
