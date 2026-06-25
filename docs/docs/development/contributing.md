# Contributing

## Local setup

```bash
git clone https://github.com/untraceablez/scryme.git
cd scryme
docker compose -f docker-compose.dev.yml up   # backend on http://localhost:8000 with hot reload
```

## Tests and linting

The backend test suite runs against PostgreSQL. Point `SCRYME_DATABASE_URL` at a reachable
database (the dev compose Postgres works) and run:

```bash
cd backend
pytest tests/         # tests + coverage (writes coverage.xml)
ruff check src tests  # lint
```

CI (GitHub Actions) runs the same checks on every pull request; a Jenkins + SonarQube pipeline adds
a coverage quality gate.

## Workflow

- Branch per change: `feat/*` → pull request into `main`.
- Keep tests green and `ruff` clean; add tests for new behavior.
- Searchable card attributes get promoted to indexed columns; everything else reads from
  `cards.raw`. Add an Alembic migration when promoting a new field.
- **Never commit personal collection data.** `tests/fixtures/*_full.csv` is gitignored; commit only
  small redacted `*_sample.csv` fixtures.

## Extending scryme

- **New import format** — add a parser in `backend/src/importers/` (`detect` + `parse`, decorated
  with `@register`) and import it in `importers/__init__.py`. See
  [Supported Formats](../import/formats.md).
- **New search filter** — add an alias in `search/parser.py` and a handler in `search/compiler.py`,
  then cover it with tests.

## Documentation

These docs are built with [MkDocs Material](https://squidfunk.github.io/mkdocs-material/) and
deployed to [docs.scryme.app](https://docs.scryme.app) from the `docs/` directory on every push to
`main`. To preview locally:

```bash
pip install -r docs/docs-requirements.txt
cd docs && mkdocs serve   # http://127.0.0.1:8001
```
