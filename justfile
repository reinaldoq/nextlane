set dotenv-load := true

default:
    @just --list

dev-api:
    uv run uvicorn api.index:app --reload --port 8000
dev-web:
    npm --prefix web run dev
db:
    supabase start
test:
    DATABASE_URL="${DATABASE_URL:-postgresql://postgres:postgres@127.0.0.1:54322/postgres}" uv run pytest -q
lint:
    uv run ruff check . && uv run ruff format --check . && npm --prefix web run lint && npm --prefix web run typecheck
gate: lint test
    npm --prefix web run build
seed:
    supabase db reset
