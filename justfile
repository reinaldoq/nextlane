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
e2e:
    npm --prefix web run e2e
seed:
    supabase db reset
pin-api:
    uv export --no-dev --no-hashes --no-emit-project --format requirements-txt -o api/requirements.txt
