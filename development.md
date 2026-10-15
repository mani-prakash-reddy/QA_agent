# FastAPI Project - Development

## Local Development

For local development, run PostgreSQL with Docker Compose, and run the FastAPI and Vite development servers locally.

Start the supporting services:

```bash
docker compose up -d db
```

Then, from the `backend` directory, install the dependencies and prepare the database:

```bash
uv sync
uv run bash scripts/prestart.sh
```

Start the FastAPI development server:

```bash
uv run fastapi dev
```

In another terminal, from the project root, install the frontend dependencies and start the Vite development server:

```bash
bun install
bun run dev
```

Now you can open these URLs:

Frontend development server: <http://localhost:5173>

Backend API: <http://localhost:8000>

Automatic interactive API documentation with Swagger UI: <http://localhost:8000/docs>

The frontend development server uses the backend at `http://localhost:8000`, as configured in `frontend/.env`.

### Frontend Served by FastAPI

Build the frontend from the `frontend` directory:

```bash
bun run build
```

The build is written to `backend/app/frontend` and served by FastAPI at <http://localhost:8000>. Rebuild the frontend after making frontend changes.

## Full Stack with Docker Compose

Everything (Postgres, backend, frontend dev server, Adminer) runs from `compose.yml`:

```bash
cp .env.example .env   # first time only, then change the secrets
docker compose watch
```

`watch` builds and starts the stack, syncs source changes into the containers (FastAPI and Vite hot-reload), and rebuilds an image when `backend/pyproject.toml`, `uv.lock`, `frontend/package.json` or `bun.lock` change. Migrations and the first superuser are created automatically on backend start.

| Service | URL |
|---|---|
| Frontend (Vite dev server) | <http://localhost:5173> |
| Backend API / Swagger docs | <http://localhost:8000> / <http://localhost:8000/docs> |
| Adminer (DB admin) | <http://localhost:8080> |
| Postgres | `localhost:5432` (db `app`, user `postgres`) |

Stop a locally running FastAPI/Vite server first, because they use the same ports.

## Database: pgvector, Apache AGE, JSONB

The `db` service is built from `db/Dockerfile`: the upstream `apache/age` Postgres 18 image plus the `pgvector` package (no official image ships both). `db/init-extensions.sql` creates the `vector` and `age` extensions on first volume init, and an Alembic migration does the same for existing databases. `ag_catalog` is appended to the database `search_path`. JSONB is a built-in Postgres type and needs no setup.

If you already had a data volume, run `docker compose down -v` to recreate it (this deletes the data).

## Email

Email is disabled in the Compose stack (no `SMTP_HOST`), so password recovery and welcome emails are skipped. Set `SMTP_*` and `EMAILS_FROM_EMAIL` on the `backend` service to enable it. The Playwright reset-password test needs a Mailpit instance and will fail without one.

## Environment Variables

Compose reads `.env` (git-ignored) for interpolation; create it from `.env.example`. Compose overrides hostnames such as the database and SMTP server with service names. Restart the stack after changing variables.

## End-to-End Tests

```bash
docker compose --profile test run --rm playwright
```

## Pre-commit Hooks and Code Linting

The project uses [prek](https://prek.j178.dev/), a modern alternative to [pre-commit](https://pre-commit.com/), for code linting and formatting.

You can find a file `.pre-commit-config.yaml` with configurations at the root of the project.

### Install `prek` to Run Automatically

`prek` is already part of the dependencies of the project.

From the project root, install the Git hook so that `prek` runs automatically before each commit:

```bash
uv run prek install -f
```

The `-f` flag forces the installation, in case there was already a `pre-commit` hook previously installed.

Now whenever you try to commit, for example with:

```bash
git commit
```

`prek` will check and format the code you are about to commit. If it modifies any files, add those files to Git again before committing.

### Run `prek` Manually

You can also run `prek` manually on all files from the project root:

```bash
uv run prek run --all-files
```
