# Repository Guidelines

## Project Structure & Module Organization
`backend/` contains the FastAPI service and analysis engine. Core code lives in `backend/perfetto_trace_analyzer/`, with analyzers under `analyzers/`, API routes under `routes/`, and generated uploads/results under `uploads/`. Backend tests live in `backend/tests/`. `frontend/` is a Vite + React TypeScript app; UI code is in `frontend/src/`, shared API types are in `frontend/src/api/`, and app state is in `frontend/src/state/`. `docs/` holds contributor and architecture notes. Treat `perfetto-mcp-tmp/` as a separate experimental package unless your change explicitly targets it.

## Build, Test, and Development Commands
Backend setup:
```bash
cd backend && pip install -e ".[dev]"
python run_server.py
```
Frontend setup:
```bash
cd frontend && npm install
npm run dev
```
Quality checks:
```bash
cd backend && pytest tests/ -v
cd frontend && npm run lint
cd frontend && npm run build
```
Container flow:
```bash
docker compose up --build
```
CI runs backend tests plus frontend lint and production build, so keep all three green.

## Coding Style & Naming Conventions
Follow the existing style in each area instead of reformatting unrelated code. Python uses 4-space indentation, snake_case names, and typed models/functions where practical. TypeScript/React files use 2-space indentation, double quotes, PascalCase component names, and camelCase helpers/hooks. Keep analyzers narrowly scoped and colocate analyzer-specific logic under `backend/perfetto_trace_analyzer/analyzers/`. Frontend linting is enforced with ESLint (`frontend/eslint.config.js`); there is no repo-wide formatter configured, so keep diffs minimal and consistent.

## Testing Guidelines
Add backend tests in `backend/tests/test_*.py`; current coverage relies on `pytest` and `hypothesis`. Prefer focused unit tests for analyzer behavior, insufficient-data handling, and parsing/scoring edge cases. For frontend changes, at minimum run `npm run lint` and `npm run build`; include manual verification notes for upload, polling, and Perfetto UI interactions when relevant.

## Commit & Pull Request Guidelines
Recent history uses Conventional Commit prefixes such as `feat:` and `fix:`. Keep commits focused and descriptive, for example `feat: add binder analyzer summary`. PRs should describe the user-visible change, list validation steps, link related issues, and include screenshots or short recordings for UI changes. If you add or change an analyzer, update `README.md`, `README_CN.md`, and `CHANGELOG.md` in the same PR.
